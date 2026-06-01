from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .exceptions import UpdateVerificationError

DEFAULT_UPDATE_REPO = "Simon-Choi-1028/linear-stage-control"
DEFAULT_SETUP_ASSET_NAME = "LinearStageControlSetup.exe"
DEFAULT_MANIFEST_ASSET_NAME = "update_manifest.json"


@dataclass(frozen=True)
class UpdateSettings:
    enabled: bool = True
    repo: str = DEFAULT_UPDATE_REPO


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    html_url: str
    setup_asset_name: str
    setup_url: str
    setup_size_bytes: int | None
    sha256: str | None
    notes: str = ""

    @property
    def can_auto_install(self) -> bool:
        return bool(self.setup_url and self.sha256)


def update_settings_from_config(config: dict[str, Any]) -> UpdateSettings:
    updates = config.get("updates", {})
    return UpdateSettings(
        enabled=bool(updates.get("enabled", True)),
        repo=str(updates.get("repo") or DEFAULT_UPDATE_REPO),
    )


def fetch_latest_update(
    repo: str,
    current_version: str,
    *,
    timeout_s: float = 10.0,
) -> UpdateInfo | None:
    try:
        release = _fetch_json(f"https://api.github.com/repos/{repo}/releases/latest", timeout_s=timeout_s)
    except Exception as exc:
        raise UpdateVerificationError("GitHub 최신 릴리즈 정보를 가져오지 못했습니다.", str(exc)) from exc
    version = str(release.get("tag_name") or release.get("name") or "").strip()
    if not version or not is_newer_version(version, current_version):
        return None

    assets = list(release.get("assets") or [])
    setup_asset = _find_asset(assets, DEFAULT_SETUP_ASSET_NAME)
    if setup_asset is None:
        setup_asset = _find_asset_by_suffix(assets, ".exe")
    if setup_asset is None:
        return UpdateInfo(
            version=version,
            html_url=str(release.get("html_url") or ""),
            setup_asset_name="",
            setup_url="",
            setup_size_bytes=None,
            sha256=None,
            notes=str(release.get("body") or ""),
        )

    manifest_asset = _find_asset(assets, DEFAULT_MANIFEST_ASSET_NAME)
    sha256 = None
    manifest_asset_name = str(setup_asset.get("name") or DEFAULT_SETUP_ASSET_NAME)
    if manifest_asset is not None:
        try:
            manifest = _fetch_json(str(manifest_asset.get("browser_download_url")), timeout_s=timeout_s)
            manifest_asset_name, sha256 = _manifest_setup_hash(manifest, manifest_asset_name)
        except Exception as exc:
            raise UpdateVerificationError("업데이트 manifest를 읽거나 해석하지 못했습니다.", str(exc)) from exc

    return UpdateInfo(
        version=version,
        html_url=str(release.get("html_url") or ""),
        setup_asset_name=manifest_asset_name,
        setup_url=str(setup_asset.get("browser_download_url") or ""),
        setup_size_bytes=_optional_int(setup_asset.get("size")),
        sha256=sha256,
        notes=str(release.get("body") or ""),
    )


def is_newer_version(latest: str, current: str) -> bool:
    return _version_key(latest) > _version_key(current)


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_file_sha256(path: str | Path, expected_sha256: str) -> bool:
    expected = re.sub(r"[^0-9a-fA-F]", "", expected_sha256 or "").lower()
    return bool(expected) and sha256_file(path).lower() == expected


def download_file(
    url: str,
    output_path: str | Path,
    *,
    progress_callback: Callable[[int, int | None], None] | None = None,
    timeout_s: float = 60.0,
) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "LinearStageControl-Updater"})
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response, path.open("wb") as file:
            total = _optional_int(response.headers.get("Content-Length"))
            received = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                file.write(chunk)
                received += len(chunk)
                if progress_callback is not None:
                    progress_callback(received, total)
    except Exception as exc:
        raise UpdateVerificationError("업데이트 설치 파일 다운로드에 실패했습니다.", str(exc)) from exc
    return path


def build_update_manifest(
    *,
    version: str,
    asset_name: str,
    sha256: str,
    size_bytes: int,
) -> dict[str, Any]:
    return {
        "version": version,
        "asset_name": asset_name,
        "sha256": sha256,
        "size_bytes": size_bytes,
    }


def _fetch_json(url: str, *, timeout_s: float) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "LinearStageControl-Updater"})
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        return json.loads(response.read().decode("utf-8"))


def _find_asset(assets: list[Any], name: str) -> dict[str, Any] | None:
    expected = name.lower()
    for asset in assets:
        if str(asset.get("name") or "").lower() == expected:
            return asset
    return None


def _find_asset_by_suffix(assets: list[Any], suffix: str) -> dict[str, Any] | None:
    suffix = suffix.lower()
    for asset in assets:
        if str(asset.get("name") or "").lower().endswith(suffix):
            return asset
    return None


def _manifest_setup_hash(manifest: dict[str, Any], default_asset_name: str) -> tuple[str, str | None]:
    asset_name = str(manifest.get("asset_name") or default_asset_name)
    sha256 = manifest.get("sha256")
    if not sha256 and isinstance(manifest.get("assets"), list):
        for asset in manifest["assets"]:
            if str(asset.get("name") or "") == asset_name:
                sha256 = asset.get("sha256")
                break
    return asset_name, str(sha256) if sha256 else None


def _version_key(version: str) -> tuple[int, int, int, str]:
    clean = version.strip().lower().lstrip("v")
    match = re.match(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?(.*)", clean)
    if not match:
        return (0, 0, 0, clean)
    major, minor, patch, suffix = match.groups()
    return (int(major or 0), int(minor or 0), int(patch or 0), suffix or "")


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
