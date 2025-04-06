import tkinter as tk

class ToolTip:
    def __init__(self, widget, text="", follow_widget=None):
        self.widget = widget
        self._text = text
        self.follow_widget = follow_widget  # 关联的另一个ToolTip对象
        self.tip_window = None
        self.widget.bind("<Enter>", self.show_tip)
        self.widget.bind("<Leave>", self.hide_tip)
    @property
    def text(self):return self._text
    @text.setter
    def text(self, value):
        self._text = value
        if isinstance(self.follow_widget, ToolTip): self.follow_widget._text = value

    def show_tip(self, event=None):
        """显示提示"""
        if self.tip_window or not self._text:return
        x = self.widget.winfo_rootx() + 30
        y = self.widget.winfo_rooty() + 20
        self.tip_window = tk.Toplevel(self.widget)
        self.tip_window.overrideredirect(True)
        self.tip_window.geometry(f"+{x}+{y}")
        label = tk.Label(
            self.tip_window,
            text=self._text,
            bg="#FFFFE0",
            relief="solid",
            borderwidth=1,
            font=("宋体", 10)
        )
        label.pack()

    def hide_tip(self, event=None):
        """隐藏提示"""
        if self.tip_window:self.tip_window.destroy()
        self.tip_window = None