import tkinter as tk

class ToolTip:
    def __init__(self, widget, text="", follow_widget=None, wrap_length=300):
        self.widget = widget
        self._text = text
        self.wrap_length = wrap_length  # 新增换行长度参数
        self.follow_widget = follow_widget
        self.tip_window = None
        self.widget.bind("<Enter>", self.show_tip)
        self.widget.bind("<Leave>", self.hide_tip)

    @property
    def text(self): return self._text

    @text.setter
    def text(self, value):
        self._text = value
        if isinstance(self.follow_widget, ToolTip):
            self.follow_widget._text = value

    def show_tip(self, event=None):
        """显示多行提示"""
        if self.tip_window or not self._text:
            return

        # 计算提示窗口位置
        x = self.widget.winfo_rootx() + 30
        y = self.widget.winfo_rooty() + 20

        # 创建提示窗口
        self.tip_window = tk.Toplevel(self.widget)
        self.tip_window.overrideredirect(True)
        self.tip_window.geometry(f"+{x}+{y}")

        # 创建支持多行显示的Label
        label = tk.Label(
            self.tip_window,
            text=self._text,
            bg="#FFFFE0",
            relief="solid",
            borderwidth=1,
            font=("宋体", 10),
            wraplength=self.wrap_length,  # 关键参数：换行宽度
            justify="left",             # 左对齐
            anchor="w"                  # 文本靠左
        )
        label.pack(padx=5, pady=3)  # 增加内边距

    def hide_tip(self, event=None):
        """隐藏提示"""
        if self.tip_window:
            self.tip_window.destroy()
        self.tip_window = None