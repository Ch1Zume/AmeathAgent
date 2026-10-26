"""Ameath's cherry-blossom palette: cream surfaces, rose accents, readable ink."""

STYLE = """
QWidget { font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 12px; color: #583a49; }
QMainWindow, QDialog { background: #fff7fa; }
QWidget#sidebar { background: #fce8f0; border-right: 1px solid #edc7d7; }
QWidget#workspace, QWidget#topbar, QWidget#assistant { background: #fffafd; }
QWidget#topbar { border-bottom: 1px solid #efd3df; }
QWidget#assistant { border-left: 1px solid #efd3df; }
QLabel#brand { font-size: 16px; font-weight: 700; color: #b43b70; }
QLabel#headline { font-size: 21px; font-weight: 600; color: #8c3159; }
QLabel#sectionTitle { font-size: 16px; font-weight: 600; color: #943d62; }
QLabel#muted { color: #886777; font-size: 11px; }
QLabel#eyebrow { color: #ad4d77; font-size: 10px; font-weight: 600; }
QLabel#badge { background: #f9dce9; color: #9b3b65; padding: 5px 9px; border-radius: 7px; font-size: 10px; }
QLabel#status { background: #fff0f5; color: #88506a; padding: 12px; border: 1px solid #efccdb; border-radius: 12px; font-size: 11px; }
QLabel#selectionText { color: #795269; font-size: 11px; }
QFrame#contextCard { background: #fff0f6; border: 1px solid #ecc6d7; border-radius: 12px; }
QPushButton { background: #fff0f6; border: 1px solid #e7b6cb; border-radius: 8px; padding: 7px 12px; color: #8c3d61; }
QPushButton:hover { background: #fbdce9; border-color: #d2779c; }
QPushButton:pressed { background: #f2c2d7; }
QPushButton:disabled { color: #aa8c99; background: #f6edf1; border-color: #e9dce2; }
QPushButton#primary { background: #c5497c; color: #ffffff; border: 1px solid #b73f70; font-weight: 600; }
QPushButton#primary:hover { background: #ae3767; }
QPushButton#primary:disabled { background: #edc9d8; color: #967786; border-color: #e2bccc; }
QPushButton#nav { text-align: left; background: transparent; border: none; padding: 13px; color: #875d72; }
QPushButton#nav:checked { background: #f7cede; color: #96365f; border-left: 2px solid #c5497c; border-radius: 5px; }
QPushButton#quiet { background: transparent; border: 1px solid #e7bfd0; font-size: 11px; padding: 6px 9px; }
QPushButton#observe:checked { background: #e4f2eb; border-color: #86b9a0; color: #336b52; }
QPushButton#observe { background: #fff0f6; color: #87586e; }
QTextBrowser { background: transparent; border: none; color: #583a49; padding: 4px; font-size: 12px; selection-background-color: #f0b9d0; }
QTextEdit, QLineEdit, QSpinBox, QComboBox { background: #ffffff; border: 1px solid #e5bdd0; border-radius: 8px; padding: 9px; color: #583a49; selection-background-color: #f3c0d6; selection-color: #63344b; }
QTextEdit:focus, QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border-color: #c5497c; }
QSpinBox { padding: 5px; }
QSpinBox QLineEdit { border: none; background: transparent; padding: 0; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView { background: #fff7fa; color: #583a49; selection-background-color: #f7cede; selection-color: #8c3159; }
QCheckBox { spacing: 8px; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #d99ab6; border-radius: 5px; background: #ffffff; }
QCheckBox::indicator:checked { background: #c5497c; border: 3px solid #f0bad1; }
QCheckBox::indicator:hover { border-color: #ad3767; }
QTabWidget::pane { background: #fffafd; border: 1px solid #ecc6d7; border-radius: 7px; }
QTabBar::tab { background: #fff0f6; color: #95677d; padding: 9px 18px; border: 1px solid #ecc6d7; }
QTabBar::tab:selected { background: #f8d7e5; color: #91355d; border-bottom-color: #c5497c; }
QScrollArea#paperScroll, QScrollArea#paperScroll > QWidget > QWidget { background: #f0e4e9; border: none; }
QWidget#readerBar { background: #fff0f6; }
QLabel#documentTitle { font-size: 12px; font-weight: 600; color: #793f5a; }
QLabel#readerHint { background: #fcebf2; color: #886777; font-size: 10px; }
QScrollBar:vertical { background: #fceef4; width: 8px; margin: 0; }
QScrollBar::handle:vertical { background: #dca2bc; border-radius: 4px; min-height: 25px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QListWidget { background: #fffafd; border: 1px solid #ecc6d7; border-radius: 10px; padding: 6px; }
QListWidget::item { border-radius: 7px; padding: 14px 9px; border-bottom: 1px solid #f1dbe5; }
QListWidget::item:selected { background: #f8d7e5; color: #813654; }
QMenu { background: #fff7fa; border: 1px solid #e1a9c2; padding: 6px; }
QMenu::item { padding: 8px 20px; border-radius: 5px; }
QMenu::item:selected { background: #f7cede; color: #8c3159; }
QToolTip { background: #fff0f6; color: #793f5a; border: 1px solid #dfa5be; padding: 6px; }
"""

MARKDOWN_STYLE = """
body { color: #583a49; font-size: 12px; line-height: 1.6; }
h2, h3 { color: #96365f; margin-top: 18px; margin-bottom: 12px; }
p { margin-top: 8px; margin-bottom: 12px; line-height: 160%; }
strong { color: #8c3159; }
a { color: #b33c70; text-decoration: none; }
blockquote { color: #886777; margin-left: 8px; }
code { color: #a83d6b; font-family: Consolas; }
"""
