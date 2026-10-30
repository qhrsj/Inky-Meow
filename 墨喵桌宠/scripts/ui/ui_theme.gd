class_name UiTheme
extends RefCounted

## 「水墨宣纸」主题：圆角纸面板 + 墨色文字 + 朱砂点缀 + 柔和阴影。
## 全部代码构建，main.gd 把 Theme 注入 UILayer/UI 与两个菜单，
## 所有面板/按钮/文本自动继承，不再用 Godot 默认灰盒子。

const PAPER := Color("f9f4e8")            # 宣纸底
const PAPER_DEEP := Color("f1e8d4")       # 深一号的纸（hover/点缀）
const CARD := Color("fdfaf1")             # 输入框等更亮的卡纸
const BORDER := Color("d8c7a2")           # 纸边
const INK := Color("3b3226")              # 墨色正文
const INK_SOFT := Color("6f6350")         # 淡墨
const CINNABAR := Color("9e2b25")         # 朱砂（标题/强调）
const CINNABAR_HOVER := Color("b23a32")
const SHADOW := Color(0.18, 0.13, 0.08, 0.20)


static func build() -> Theme:
	var t := Theme.new()
	t.set_default_font_size(16)

	# 面板：圆角纸 + 描边 + 柔影
	t.set_stylebox("panel", "PanelContainer", panel_box())

	# 按钮：纸片圆角，hover 染朱
	t.set_stylebox("normal", "Button", button_box())
	var hov := button_box(PAPER_DEEP)
	hov.border_color = CINNABAR
	t.set_stylebox("hover", "Button", hov)
	var pressed := button_box(PAPER_DEEP)
	pressed.expand_margin_top = 1.0
	t.set_stylebox("pressed", "Button", pressed)
	var disabled := button_box()
	disabled.bg_color = Color(PAPER.r, PAPER.g, PAPER.b, 0.5)
	disabled.border_color = Color(BORDER.r, BORDER.g, BORDER.b, 0.5)
	t.set_stylebox("disabled", "Button", disabled)
	t.set_color("font_color", "Button", INK)
	t.set_color("font_hover_color", "Button", CINNABAR)
	t.set_color("font_pressed_color", "Button", CINNABAR)
	t.set_color("font_disabled_color", "Button", INK_SOFT)

	# 文本
	t.set_color("font_color", "Label", INK)
	t.set_color("default_color", "RichTextLabel", INK)
	t.set_color("font_color", "TextEdit", INK)
	t.set_color("caret_color", "TextEdit", CINNABAR)
	t.set_color("font_placeholder_color", "TextEdit", Color(INK_SOFT.r, INK_SOFT.g, INK_SOFT.b, 0.55))
	t.set_color("font_selected_color", "TextEdit", PAPER)
	t.set_color("selection_color", "TextEdit", Color(CINNABAR.r, CINNABAR.g, CINNABAR.b, 0.35))
	t.set_stylebox("normal", "TextEdit", edit_box())
	t.set_stylebox("focus", "TextEdit", edit_box(true))
	t.set_stylebox("read_only", "TextEdit", edit_box())

	# 右键/托盘菜单
	t.set_stylebox("panel", "PopupMenu", panel_box(10))
	var menu_hov := StyleBoxFlat.new()
	menu_hov.bg_color = PAPER_DEEP
	menu_hov.set_corner_radius_all(6)
	menu_hov.content_margin_left = 8.0
	menu_hov.content_margin_right = 8.0
	t.set_stylebox("hover", "PopupMenu", menu_hov)
	t.set_color("font_color", "PopupMenu", INK)
	t.set_color("font_hover_color", "PopupMenu", CINNABAR)
	t.set_color("font_disabled_color", "PopupMenu", INK_SOFT)
	t.set_constant("v_separation", "PopupMenu", 6)

	# 分隔线：淡墨细线
	var sep := StyleBoxFlat.new()
	sep.bg_color = Color(BORDER.r, BORDER.g, BORDER.b, 0.7)
	sep.content_margin_top = 5.0
	sep.content_margin_bottom = 5.0
	t.set_stylebox("separator", "HSeparator", sep)
	return t


static func panel_box(radius: float = 14.0) -> StyleBoxFlat:
	var sb := StyleBoxFlat.new()
	sb.bg_color = PAPER
	sb.set_corner_radius_all(int(radius))
	sb.border_color = BORDER
	sb.set_border_width_all(1)
	sb.shadow_color = SHADOW
	sb.shadow_size = 9
	sb.shadow_offset = Vector2(0, 3)
	sb.content_margin_left = 14.0
	sb.content_margin_right = 14.0
	sb.content_margin_top = 12.0
	sb.content_margin_bottom = 12.0
	return sb


static func button_box(bg: Color = PAPER, border: Color = BORDER) -> StyleBoxFlat:
	var sb := StyleBoxFlat.new()
	sb.bg_color = bg
	sb.set_corner_radius_all(8)
	sb.border_color = border
	sb.set_border_width_all(1)
	sb.content_margin_left = 10.0
	sb.content_margin_right = 10.0
	sb.content_margin_top = 4.0
	sb.content_margin_bottom = 4.0
	return sb


static func edit_box(focused: bool = false) -> StyleBoxFlat:
	var sb := StyleBoxFlat.new()
	sb.bg_color = CARD
	sb.set_corner_radius_all(8)
	sb.border_color = CINNABAR if focused else BORDER
	sb.set_border_width_all(1 if not focused else 2)
	sb.content_margin_left = 10.0
	sb.content_margin_right = 10.0
	sb.content_margin_top = 8.0
	sb.content_margin_bottom = 8.0
	return sb
