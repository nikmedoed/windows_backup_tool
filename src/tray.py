import math
from pathlib import Path
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets

from src.config import Settings
from src.i18n import _, install_qt
from src.copier import run_backup

_SPIN_STEPS = 12
_SPIN_INTERVAL_MS = 140


class _BackupWorker(QtCore.QObject):
    finished = QtCore.Signal(bool)
    progress = QtCore.Signal(int, int)

    def __init__(self, cfg: Settings):
        super().__init__()
        self._cfg = cfg

    @QtCore.Slot()
    def run(self) -> None:
        success = run_backup(self._cfg, progress_cb=self.progress.emit)
        self.finished.emit(success)

class _OverlayBubble(QtWidgets.QWidget):
    def __init__(self, title: str, message: str, icon: QtGui.QIcon):
        flags = (
            QtCore.Qt.WindowType.Tool
            | QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
        )
        super().__init__(None, flags)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowFlag(QtCore.Qt.WindowType.WindowDoesNotAcceptFocus, True)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        frame = QtWidgets.QFrame()
        frame.setStyleSheet(
            """
            QFrame {
                background-color: rgba(12, 14, 22, 215);
                border-radius: 14px;
                color: white;
                padding: 14px 18px;
                font-size: 13px;
            }
            """
        )
        inner = QtWidgets.QHBoxLayout(frame)
        inner.setContentsMargins(4, 4, 4, 4)
        inner.setSpacing(12)
        icon_lbl = QtWidgets.QLabel()
        icon_lbl.setPixmap(icon.pixmap(32, 32))
        text_lbl = QtWidgets.QLabel(f"<b>{title}</b><br>{message}")
        text_lbl.setTextFormat(QtCore.Qt.TextFormat.RichText)
        text_lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignVCenter)
        inner.addWidget(icon_lbl)
        inner.addWidget(text_lbl)
        layout.addWidget(frame)

        self._fade_timer = QtCore.QTimer(self)
        self._fade_timer.setSingleShot(True)
        self._fade_timer.timeout.connect(self._fade_out)
        self._animation: Optional[QtCore.QPropertyAnimation] = None

    def show_for(self, duration_ms: int = 2500) -> None:
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self._start_animation(1.0, 220)
        self._fade_timer.start(duration_ms)

    def _start_animation(self, target: float, duration: int) -> None:
        self._animation = QtCore.QPropertyAnimation(self, b"windowOpacity", self)
        self._animation.setDuration(duration)
        self._animation.setStartValue(self.windowOpacity())
        self._animation.setEndValue(target)
        self._animation.start()

    def _fade_out(self) -> None:
        if not self.isVisible():
            return
        anim = QtCore.QPropertyAnimation(self, b"windowOpacity", self)
        anim.setDuration(320)
        anim.setStartValue(self.windowOpacity())
        anim.setEndValue(0.0)
        anim.finished.connect(self.close)
        anim.start(QtCore.QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        self._animation = anim

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        screen = QtWidgets.QApplication.primaryScreen()
        geom = screen.availableGeometry() if screen else QtCore.QRect(0, 0, 800, 600)
        margin = 24
        self.move(
            geom.right() - self.width() - margin,
            geom.top() + margin,
        )


class _TrayController(QtCore.QObject):
    def __init__(self, *, show_overlay: bool) -> None:
        super().__init__()
        self.success: Optional[bool] = None
        self._frames = self._build_spinner_frames()
        self._frame_index = 0
        self._progress_text = _("Starting backup…")
        self._progress_ratio = 0.0
        self._tray = QtWidgets.QSystemTrayIcon(self._compose_icon(self._frames[0]), self)
        self._tray.setToolTip(self._progress_text)
        self._tray.setVisible(True)
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._advance_frame)
        self._timer.start(_SPIN_INTERVAL_MS)
        self._overlay: Optional[_OverlayBubble] = None
        self._show_overlay = show_overlay

    def _build_spinner_frames(self) -> list[QtGui.QPixmap]:
        size = 64
        frames: list[QtGui.QPixmap] = []
        for step in range(_SPIN_STEPS):
            pix = QtGui.QPixmap(size, size)
            pix.fill(QtCore.Qt.GlobalColor.transparent)
            painter = QtGui.QPainter(pix)
            painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
            # background circle
            base_color = QtGui.QColor("#2F7DEB")
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(QtGui.QBrush(base_color.darker(120)))
            painter.drawEllipse(0, 0, size, size)

            # rotating indicator
            painter.setBrush(QtGui.QBrush(QtGui.QColor("#FFFFFF")))
            angle = (2 * math.pi / _SPIN_STEPS) * step
            cx = cy = size / 2
            radius = size * 0.35
            dot_radius = size * 0.12
            x = cx + radius * math.cos(angle) - dot_radius
            y = cy + radius * math.sin(angle) - dot_radius
            painter.drawEllipse(QtCore.QRectF(x, y, dot_radius * 2, dot_radius * 2))
            painter.end()
            frames.append(pix)
        return frames

    def _compose_icon(self, frame_pix: QtGui.QPixmap) -> QtGui.QIcon:
        pix = QtGui.QPixmap(frame_pix)
        painter = QtGui.QPainter(pix)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        padding = 10
        inner_rect = QtCore.QRectF(padding, padding, pix.width() - padding * 2, pix.height() - padding * 2)
        # background ring
        painter.setBrush(QtGui.QBrush(QtGui.QColor("#123456")))
        painter.setOpacity(0.55)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.drawEllipse(inner_rect)
        # filled portion
        painter.setOpacity(0.85)
        painter.setBrush(QtGui.QBrush(QtGui.QColor("#73D7FF")))
        start_angle = 90 * 16  # start at top
        span = -int(360 * 16 * self._progress_ratio)
        painter.drawPie(inner_rect, start_angle, span)
        painter.end()
        return QtGui.QIcon(pix)

    @QtCore.Slot(int, int)
    def update_progress(self, done: int, total: int) -> None:
        pct = int(done / total * 100) if total else 100
        self._progress_ratio = min(max(done / total, 0.0), 1.0) if total else 1.0
        self._progress_text = _("Backup in progress ({pct}%)").format(pct=pct)
        self._tray.setToolTip(self._progress_text)

    @QtCore.Slot(bool)
    def finish(self, success: bool) -> None:
        self.success = success
        self._timer.stop()
        result_icon = _resolve_base_icon(success=success)
        self._tray.setIcon(result_icon)
        title = _("Backup completed") if success else _("Backup failed")
        message = _("Done. You can close this notification.") if success else _("Check logs for details.")
        self._tray.setToolTip(title)
        self._tray.showMessage(title, message, result_icon, 3000)
        if self._show_overlay:
            self._overlay = _OverlayBubble(title, message, result_icon)
            self._overlay.show_for(2600)
        QtCore.QTimer.singleShot(1500, self._cleanup)

    def _advance_frame(self) -> None:
        frame = self._frames[self._frame_index]
        self._tray.setIcon(self._compose_icon(frame))
        self._frame_index = (self._frame_index + 1) % len(self._frames)

    def _cleanup(self) -> None:
        self._tray.setVisible(False)
        QtWidgets.QApplication.quit()


def _resolve_base_icon(*, success: Optional[bool] = None) -> QtGui.QIcon:
    icon_dir = Path(__file__).resolve().parent.parent / "icon"
    ico = icon_dir / "icon.ico"
    png = icon_dir / "icon.png"
    path = ico if ico.exists() else png if png.exists() else None
    if path:
        icon = QtGui.QIcon(str(path))
    else:
        style = QtWidgets.QApplication.style() if QtWidgets.QApplication.instance() else None
        icon = style.standardIcon(QtWidgets.QStyle.StandardPixmap.SP_DriveHDIcon) if style else QtGui.QIcon()
    if success is None:
        return icon
    # overlay tinted badge to distinguish success/error
    size = 64
    pix = QtGui.QPixmap(icon.pixmap(size, size)) if not icon.isNull() else QtGui.QPixmap(size, size)
    if pix.isNull():
        pix = QtGui.QPixmap(size, size)
    painter = QtGui.QPainter(pix)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    color = "#1DC74C" if success else "#E0483E"
    painter.setBrush(QtGui.QBrush(QtGui.QColor(color)))
    painter.setOpacity(0.4)
    painter.setPen(QtCore.Qt.PenStyle.NoPen)
    painter.drawEllipse(4, 4, size - 8, size - 8)
    painter.end()
    return QtGui.QIcon(pix)


def run_with_tray(cfg: Settings) -> bool:
    """
    Run backup with a temporary tray icon spinner, suppressing the console window.
    """
    app = QtWidgets.QApplication.instance()
    owns_app = app is None
    if owns_app:
        app = QtWidgets.QApplication([])
        QtWidgets.QApplication.setQuitOnLastWindowClosed(False)
        install_qt(app)

    controller = _TrayController(show_overlay=cfg.show_overlay)
    worker = _BackupWorker(cfg)
    thread = QtCore.QThread()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(controller.finish)
    worker.finished.connect(thread.quit)
    worker.finished.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)
    worker.progress.connect(controller.update_progress)

    thread.start()
    app.exec()
    return bool(controller.success)
