from __future__ import annotations


APP_STYLESHEET = """
QWidget {
    color: #172033;
    font-size: 9pt;
}
QMainWindow {
    background: #f6f8fb;
}
QDialog, QDialog QWidget {
    background: #f6f8fb;
    color: #172033;
}
QDialog QLabel {
    background: transparent;
    color: #172033;
}
QWidget#topToolbar {
    background: #ffffff;
    border-bottom: 1px solid #e1e7ef;
}
QWidget#controlPanel, QWidget#previewPanel {
    background: #f6f8fb;
}
QGroupBox {
    background: #ffffff;
    border: 1px solid #dde5ee;
    border-radius: 8px;
    margin-top: 18px;
    padding: 15px 12px 12px 12px;
    font-weight: 700;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 11px;
    padding: 0 7px;
    color: #111827;
    background: #f6f8fb;
}
QWidget#parameterRow, QWidget#parameterRow QWidget {
    background: transparent;
}
QPushButton {
    background: #ffffff;
    border: 1px solid #cfd8e3;
    border-radius: 6px;
    padding: 7px 11px;
    min-height: 25px;
    color: #172033;
    font-weight: 600;
}
QPushButton:hover {
    background: #f1f6f4;
    border-color: #8eb8a7;
}
QPushButton:pressed {
    background: #e4eee9;
}
QPushButton:disabled {
    color: #98a2b3;
    background: #eef2f6;
    border-color: #dbe2ea;
}
QPushButton[variant="primary"] {
    background: #28775b;
    color: #ffffff;
    border-color: #28775b;
}
QPushButton[variant="primary"]:hover {
    background: #22664e;
    border-color: #22664e;
}
QPushButton[variant="danger"] {
    background: #ffffff;
    color: #a33a36;
    border-color: #e4b5b2;
}
QPushButton[variant="danger"]:hover {
    background: #fff3f2;
    border-color: #d98f8a;
}
QPushButton[variant="quiet"] {
    background: #f8fafc;
    color: #475467;
}
QPushButton#parameterButton {
    background: #ffffff;
    border: 1px solid #c7d0dc;
    border-radius: 5px;
    padding: 3px 0;
    min-height: 23px;
    font-weight: 700;
    font-size: 8pt;
}
QPushButton#parameterButton:hover {
    background: #edf7f2;
    border-color: #79aa96;
}
QPushButton#runControlButton {
    min-height: 44px;
    font-weight: 800;
    font-size: 10pt;
}
QLabel#parameterLabel {
    min-width: 58px;
    font-weight: 700;
    color: #263242;
}
QCheckBox {
    spacing: 7px;
    color: #263242;
}
QWidget#formatBox, QWidget#optionBox {
    background: #f8fafc;
    border: 1px solid #d8e1eb;
    border-radius: 7px;
}
QLabel#cameraScanState {
    border: 1px solid #d5dde7;
    border-radius: 11px;
    padding: 5px 11px;
    min-width: 54px;
    font-weight: 800;
    qproperty-alignment: AlignCenter;
}
QLabel#cameraScanState[state="idle"] {
    background: #f8fafc;
    color: #536171;
}
QLabel#cameraScanState[state="searching"] {
    background: #eaf2ff;
    border-color: #9dbbe8;
    color: #24568f;
}
QLabel#cameraScanState[state="success"] {
    background: #eaf7f0;
    border-color: #91c4a8;
    color: #1f5f43;
}
QLabel#cameraScanState[state="failure"] {
    background: #fff0ef;
    border-color: #de9d98;
    color: #8a2b26;
}
QLabel#cameraStatus[state="idle"] { color: #536171; }
QLabel#cameraStatus[state="searching"] { color: #24568f; font-weight: 700; }
QLabel#cameraStatus[state="success"] { color: #1f5f43; font-weight: 700; }
QLabel#cameraStatus[state="failure"] { color: #8a2b26; font-weight: 800; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit {
    background: #ffffff;
    border: 1px solid #cfd8e3;
    border-radius: 6px;
    padding: 5px 7px;
    selection-background-color: #bfe7d4;
    selection-color: #10261d;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus {
    border: 1px solid #28775b;
    background: #ffffff;
}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {
    background: #eef2f6;
    color: #667085;
}
QComboBox::drop-down {
    width: 22px;
    border: 0;
}
QScrollArea, QSplitter {
    background: #f6f8fb;
    border: 0;
}
QSplitter::handle {
    background: #e6ecf3;
}
QHeaderView::section {
    background: #f4f7fa;
    color: #253044;
    border: 0;
    border-right: 1px solid #e3e9f1;
    border-bottom: 1px solid #e3e9f1;
    padding: 7px 6px;
    font-weight: 800;
}
QTableWidget {
    background: #ffffff;
    alternate-background-color: #fbfcfe;
    border: 1px solid #dbe3ed;
    border-radius: 7px;
    gridline-color: #edf1f6;
    selection-background-color: #dff2e8;
    selection-color: #172033;
}
QTableWidget::item {
    padding: 5px;
}
QTableWidget::item:selected {
    background: #dff2e8;
    color: #172033;
}
QLabel#preview {
    background: #111418;
    color: #cfd6df;
    border-radius: 9px;
    border: 1px solid #202833;
}
QTableWidget#errorSummary, QTableWidget#previewMetrics, QTableWidget#stageSpecs, QTableWidget#preflightTable, QTableWidget#diagnosticsTable {
    background: #ffffff;
    border: 1px solid #dbe3ed;
    border-radius: 7px;
    gridline-color: #e4ebf2;
}
QTableWidget#errorSummary::item, QTableWidget#previewMetrics::item, QTableWidget#stageSpecs::item, QTableWidget#preflightTable::item, QTableWidget#diagnosticsTable::item {
    padding: 6px;
    font-weight: 700;
}
QLabel#positionStatus, QLabel#previewInfo, QLabel#errorBasis, QLabel#runStatus, QLabel#manualStageStatus, QLabel#liveStatus, QLabel#updateStatus {
    background: #ffffff;
    border: 1px solid #dbe3ed;
    border-radius: 7px;
    padding: 7px 9px;
    color: #435164;
}
QLabel#positionStatus[state="ok"] {
    background: #eef8f2;
    border-color: #91c4a8;
    color: #1f5f43;
    font-weight: 700;
}
QLabel#positionStatus[state="warning"] {
    background: #fff8df;
    border-color: #d6b95e;
    color: #6d560b;
    font-weight: 700;
}
QLabel#positionStatus[state="error"] {
    background: #fff0ef;
    border-color: #d99a96;
    color: #8a2b26;
    font-weight: 800;
}
QLabel#errorBasis, QLabel#runStatus {
    font-weight: 700;
}
QLabel#progressDetail {
    color: #667085;
    padding: 2px 4px;
}
QProgressBar {
    background: #eef2f6;
    border: 1px solid #d9e2ec;
    border-radius: 6px;
    text-align: center;
    height: 20px;
    color: #253044;
    font-weight: 700;
}
QProgressBar::chunk {
    background: #28775b;
    border-radius: 5px;
}
QTabWidget::pane {
    background: #ffffff;
    border: 1px solid #dbe3ed;
    border-radius: 8px;
    top: -1px;
}
QTabBar::tab {
    background: #eef2f6;
    border: 1px solid #dbe3ed;
    border-bottom: 0;
    border-top-left-radius: 7px;
    border-top-right-radius: 7px;
    padding: 8px 14px;
    margin-right: 3px;
    color: #536171;
    font-weight: 700;
}
QTabBar::tab:selected {
    background: #ffffff;
    color: #172033;
    border-color: #dbe3ed;
}
QSlider::groove:horizontal {
    height: 5px;
    border-radius: 3px;
    background: #d9e2ec;
}
QSlider::handle:horizontal {
    width: 15px;
    height: 15px;
    margin: -5px 0;
    border-radius: 8px;
    background: #28775b;
}
QSlider::sub-page:horizontal {
    background: #9dcbb8;
    border-radius: 3px;
}
QScrollBar:vertical {
    background: transparent;
    width: 8px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #b8c3d1;
    border-radius: 4px;
    min-height: 28px;
}
QScrollBar::handle:vertical:hover {
    background: #98a5b5;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: transparent;
}
QScrollBar:horizontal {
    background: transparent;
    height: 8px;
    margin: 0;
}
QScrollBar::handle:horizontal {
    background: #b8c3d1;
    border-radius: 4px;
    min-width: 28px;
}
QScrollBar::handle:horizontal:hover {
    background: #98a5b5;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    background: transparent;
}
QToolTip {
    background: #172033;
    color: #ffffff;
    border: 0;
    border-radius: 5px;
    padding: 6px 8px;
}
"""
