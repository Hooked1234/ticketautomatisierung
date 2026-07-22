"""Application-wide Windows-friendly design system."""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

from ticketpilot import __version__

COLORS = {
    "nav": "#15243A",
    "nav_hover": "#203552",
    "primary": "#075EA8",
    "primary_hover": "#064E8A",
    "focus": "#0B78D1",
    "surface": "#FFFFFF",
    "canvas": "#F3F6F9",
    "border": "#CBD5E1",
    "text": "#172033",
    "muted": "#526176",
    "success": "#16794D",
    "success_soft": "#E6F4ED",
    "warning": "#8A5600",
    "warning_soft": "#FFF3D6",
    "danger": "#A52A2A",
    "danger_soft": "#FCE8E8",
    "info_soft": "#E7F2FC",
}


def configure_application(app: QApplication) -> None:
    """Apply palette, readable font and DPI-scaled Qt stylesheet.

    Qt 6 enables high-DPI scaling by default; all geometry remains in logical
    pixels and no fixed device-pixel calculations are used.
    """

    app.setApplicationName("TicketPilot")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("TicketPilot")
    app.setStyle("Fusion")
    font = QFont("Segoe UI")
    font.setPointSize(10)
    app.setFont(font)

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(COLORS["canvas"]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(COLORS["text"]))
    palette.setColor(QPalette.ColorRole.Base, QColor(COLORS["surface"]))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#F7F9FB"))
    palette.setColor(QPalette.ColorRole.Text, QColor(COLORS["text"]))
    palette.setColor(QPalette.ColorRole.Button, QColor(COLORS["surface"]))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(COLORS["text"]))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(COLORS["primary"]))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
    palette.setColor(QPalette.ColorRole.Link, QColor(COLORS["primary"]))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#64748B"))
    app.setPalette(palette)
    app.setStyleSheet(_STYLESHEET)


_STYLESHEET = f"""
QWidget {{
    color: {COLORS['text']};
}}
QMainWindow, QWidget#AppCanvas {{
    background: {COLORS['canvas']};
}}
QWidget#ContentSurface, QFrame[card="true"], QGroupBox {{
    background: {COLORS['surface']};
    border: 1px solid {COLORS['border']};
    border-radius: 8px;
}}
QGroupBox {{
    font-weight: 600;
    margin-top: 12px;
    padding: 18px 12px 12px 12px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 4px;
}}
QLabel[role="pageTitle"] {{
    font-size: 22px;
    font-weight: 600;
}}
QLabel[role="sectionTitle"] {{
    font-size: 14px;
    font-weight: 600;
}}
QLabel[role="muted"] {{
    color: {COLORS['muted']};
}}
QLabel[role="metric"] {{
    font-size: 25px;
    font-weight: 600;
}}
QLabel[role="badge"] {{
    border-radius: 9px;
    padding: 2px 8px;
    font-weight: 600;
}}
QLabel[tone="success"] {{ color: {COLORS['success']}; background: {COLORS['success_soft']}; }}
QLabel[tone="warning"] {{ color: {COLORS['warning']}; background: {COLORS['warning_soft']}; }}
QLabel[tone="danger"] {{ color: {COLORS['danger']}; background: {COLORS['danger_soft']}; }}
QLabel[tone="info"] {{ color: {COLORS['primary']}; background: {COLORS['info_soft']}; }}
QLabel[role="badge"][tone="warning"] {{ background: #FFE7AD; }}
QLabel[role="badge"][tone="danger"] {{ background: #F8CECE; }}
QLabel[role="badge"][tone="success"] {{ background: #CFEBDD; }}
QFrame[notice="true"][tone="success"] {{ background: {COLORS['success_soft']}; border-left: 4px solid {COLORS['success']}; }}
QFrame[notice="true"][tone="warning"] {{ background: {COLORS['warning_soft']}; border-left: 4px solid {COLORS['warning']}; }}
QFrame[notice="true"][tone="danger"] {{ background: {COLORS['danger_soft']}; border-left: 4px solid {COLORS['danger']}; }}
QFrame[notice="true"][tone="info"] {{ background: {COLORS['info_soft']}; border-left: 4px solid {COLORS['primary']}; }}
QPushButton, QToolButton {{
    background: {COLORS['surface']};
    border: 1px solid #A9B6C6;
    border-radius: 5px;
    min-height: 30px;
    padding: 2px 12px;
}}
QPushButton:hover, QToolButton:hover {{ background: #F0F5FA; border-color: #728399; }}
QPushButton:pressed, QToolButton:pressed {{ background: #E2EAF2; }}
QPushButton:focus, QToolButton:focus, QLineEdit:focus, QTextEdit:focus,
QPlainTextEdit:focus, QComboBox:focus, QListView:focus, QTableView:focus,
QDateEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border: 2px solid {COLORS['focus']}; }}
QPushButton[primary="true"] {{
    color: #FFFFFF;
    background: {COLORS['primary']};
    border-color: {COLORS['primary']};
    font-weight: 600;
}}
QPushButton[primary="true"]:hover {{ background: {COLORS['primary_hover']}; }}
QPushButton[danger="true"] {{ color: {COLORS['danger']}; border-color: {COLORS['danger']}; }}
QPushButton:disabled, QToolButton:disabled {{ color: #7A8798; background: #E8EDF2; border-color: #D2DAE3; }}
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QDateEdit, QSpinBox, QDoubleSpinBox {{
    background: {COLORS['surface']};
    border: 1px solid #98A8BA;
    border-radius: 5px;
    min-height: 30px;
    padding: 2px 7px;
    selection-background-color: {COLORS['primary']};
}}
QTextEdit, QPlainTextEdit {{ padding: 7px; }}
QComboBox::drop-down {{ border: 0; width: 28px; }}
QHeaderView::section {{
    color: #26364A;
    background: #E8EEF4;
    border: 0;
    border-bottom: 1px solid #AFC0D0;
    padding: 8px;
    font-weight: 600;
}}
QTableView, QTableWidget {{
    background: {COLORS['surface']};
    alternate-background-color: #F7F9FB;
    border: 1px solid {COLORS['border']};
    border-radius: 6px;
    gridline-color: #E1E7EE;
    selection-background-color: #D5E9FA;
    selection-color: {COLORS['text']};
}}
QTableView::item, QTableWidget::item {{ padding: 7px; }}
QTabWidget::pane {{ background: {COLORS['surface']}; border: 1px solid {COLORS['border']}; border-radius: 6px; }}
QTabBar::tab {{ background: #E8EEF4; border: 1px solid {COLORS['border']}; padding: 8px 14px; }}
QTabBar::tab:selected {{ background: {COLORS['surface']}; color: {COLORS['primary']}; border-bottom-color: {COLORS['surface']}; }}
QWidget#NavigationPanel {{ background: {COLORS['nav']}; color: #FFFFFF; }}
QListWidget#Navigation {{
    color: #FFFFFF;
    background: {COLORS['nav']};
    border: 0;
    outline: 0;
}}
QListWidget#Navigation::item {{ min-height: 42px; padding: 3px 14px; border-left: 4px solid transparent; }}
QListWidget#Navigation::item:hover {{ background: {COLORS['nav_hover']}; }}
QListWidget#Navigation::item:selected {{ background: {COLORS['nav_hover']}; border-left-color: #5CB6FF; }}
QProgressBar {{ border: 1px solid {COLORS['border']}; border-radius: 4px; text-align: center; background: #E8EEF4; }}
QProgressBar::chunk {{ background: {COLORS['primary']}; border-radius: 3px; }}
QScrollBar:vertical {{ width: 13px; background: #EDF1F5; }}
QScrollBar::handle:vertical {{ background: #9EADBC; min-height: 28px; border-radius: 5px; }}
QToolTip {{ color: {COLORS['text']}; background: #FFFFE8; border: 1px solid #8D8D70; padding: 4px; }}
"""
