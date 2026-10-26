from PySide6.QtGui import QColor, QFont, QTextBlockFormat, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QTextBrowser


class SafeTextBrowser(QTextBrowser):
    """Model and document text cannot load file:// or remote image resources."""
    def loadResource(self, resource_type, name):
        return None

    def setMarkdown(self, markdown):
        super().setMarkdown(markdown)
        document = self.document()
        document.setDefaultFont(QFont("Microsoft YaHei UI", 10))
        block = document.begin()
        anchors = []
        while block.isValid():
            cursor = QTextCursor(block)
            block_format = block.blockFormat()
            heading = block_format.headingLevel()
            block_format.setTopMargin(14 if heading else 3)
            block_format.setBottomMargin(10)
            block_format.setLineHeight(150, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
            cursor.setBlockFormat(block_format)
            if heading:
                char_format = QTextCharFormat()
                char_format.setFontPointSize(12)
                char_format.setFontWeight(QFont.Weight.DemiBold)
                char_format.setForeground(QColor("#ead7fc"))
                cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
                cursor.mergeCharFormat(char_format)
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                if fragment.isValid() and fragment.charFormat().isAnchor():
                    anchors.append((fragment.position(), fragment.length()))
                iterator += 1
            block = block.next()
        for position, length in anchors:
            cursor = QTextCursor(document)
            cursor.setPosition(position)
            cursor.setPosition(position + length, QTextCursor.MoveMode.KeepAnchor)
            fmt = QTextCharFormat()
            fmt.setForeground(QColor("#d1b1ef"))
            fmt.setFontUnderline(False)
            cursor.mergeCharFormat(fmt)
        self.verticalScrollBar().setValue(0)
