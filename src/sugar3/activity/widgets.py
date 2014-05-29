# Copyright (C) 2009, Aleksey Lim, Simon Schampijer
# Copyright (C) 2012, Walter Bender
# Copyright (C) 2012, One Laptop Per Child
#
# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 2 of the License, or (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this library; if not, write to the
# Free Software Foundation, Inc., 59 Temple Place - Suite 330,
# Boston, MA 02111-1307, USA.

from gi.repository import Gdk
from gi.repository import Gtk
from gi.repository import cairo
from gi.repository import GObject
from gi.repository import GdkPixbuf
from gi.repository import Pango
import gettext
import logging
import math
import re
import os
import random
from sugar3.graphics.toolbutton import ToolButton
from sugar3.graphics.toolbarbox import ToolbarButton
from sugar3.graphics.toggletoolbutton import ToggleToolButton
from sugar3.graphics import tray
from jarabe.frame.framewindow import FrameWindow
from jarabe.frame.clipboardtray import ClipboardTray
from jarabe.frame.friendstray import FriendsTray
from sugar3.graphics.radiopalette import RadioPalette, RadioMenuButton
from sugar3.graphics.radiotoolbutton import RadioToolButton
from sugar3.graphics.xocolor import XoColor
from sugar3.graphics.icon import Icon
from sugar3.bundle.activitybundle import get_bundle_instance
from sugar3.graphics import style
from sugar3.graphics.alert import NotifyAlert
from sugar3.graphics.palettemenu import PaletteMenuBox
from sugar3.graphics.palette import Palette
from sugar3.graphics.palette import MouseSpeedDetector

from sugar3 import profile


from telepathy.interfaces import CHANNEL_INTERFACE
from telepathy.interfaces import CHANNEL_INTERFACE_GROUP
from telepathy.interfaces import CHANNEL_TYPE_TEXT
from telepathy.interfaces import CONN_INTERFACE_ALIASING
from telepathy.constants import CHANNEL_GROUP_FLAG_CHANNEL_SPECIFIC_HANDLES
from telepathy.constants import CHANNEL_TEXT_MESSAGE_TYPE_NORMAL
from telepathy.client import Connection
from telepathy.client import Channel


_ = lambda msg: gettext.dgettext('sugar-toolkit-gtk3', msg)


def _create_activity_icon(metadata):
    if metadata is not None and metadata.get('icon-color'):
        color = XoColor(metadata['icon-color'])
    else:
        color = profile.get_color()

    from sugar3.activity.activity import get_bundle_path
    bundle = get_bundle_instance(get_bundle_path())
    icon = Icon(file=bundle.get_icon(), xo_color=color)

    return icon


class ActivityButton(ToolButton):

    def __init__(self, activity, **kwargs):
        ToolButton.__init__(self, **kwargs)

        icon = _create_activity_icon(activity.metadata)
        self.set_icon_widget(icon)
        icon.show()

        self.props.hide_tooltip_on_click = False
        self.palette_invoker.props.toggle_palette = True
        self.props.tooltip = activity.metadata['title']
        activity.metadata.connect('updated', self.__jobject_updated_cb)

    def __jobject_updated_cb(self, jobject):
        self.props.tooltip = jobject['title']


class ActivityToolbarButton(ToolbarButton):

    def __init__(self, activity, **kwargs):
        toolbar = ActivityToolbar(activity, orientation_left=True)

        ToolbarButton.__init__(self, page=toolbar, **kwargs)

        icon = _create_activity_icon(activity.metadata)
        self.set_icon_widget(icon)
        icon.show()


class StopButton(ToolButton):

    def __init__(self, activity, **kwargs):
        ToolButton.__init__(self, 'activity-stop', **kwargs)
        self.props.tooltip = _('Stop')
        self.props.accelerator = '<Ctrl>Q'
        self.connect('clicked', self.__stop_button_clicked_cb, activity)

    def __stop_button_clicked_cb(self, button, activity):
        activity.close()


class UndoButton(ToolButton):

    def __init__(self, **kwargs):
        ToolButton.__init__(self, 'edit-undo', **kwargs)
        self.props.tooltip = _('Undo')
        self.props.accelerator = '<Ctrl>Z'


class RedoButton(ToolButton):

    def __init__(self, **kwargs):
        ToolButton.__init__(self, 'edit-redo', **kwargs)
        self.props.tooltip = _('Redo')


class CopyButton(ToolButton):

    def __init__(self, **kwargs):
        ToolButton.__init__(self, 'edit-copy', **kwargs)
        self.props.tooltip = _('Copy')
        self.props.accelerator = '<Ctrl>C'


class PasteButton(ToolButton):

    def __init__(self, **kwargs):
        ToolButton.__init__(self, 'edit-paste', **kwargs)
        self.props.tooltip = _('Paste')
        self.props.accelerator = '<Ctrl>V'


class ShareButton(RadioMenuButton):

    def __init__(self, activity, **kwargs):
        palette = RadioPalette()

        self.private = RadioToolButton(
            icon_name='zoom-home')
        palette.append(self.private, _('Private'))

        self.neighborhood = RadioToolButton(
            icon_name='zoom-neighborhood',
            group=self.private)
        self._neighborhood_handle = self.neighborhood.connect(
            'clicked', self.__neighborhood_clicked_cb, activity)
        palette.append(self.neighborhood, _('My Neighborhood'))

        activity.connect('shared', self.__update_share_cb)
        activity.connect('joined', self.__update_share_cb)

        RadioMenuButton.__init__(self, **kwargs)
        self.props.palette = palette
        if activity.max_participants == 1:
            self.props.sensitive = False

    def __neighborhood_clicked_cb(self, button, activity):
        activity.share()

    def __update_share_cb(self, activity):
        self.neighborhood.handler_block(self._neighborhood_handle)
        try:
            if activity.shared_activity is not None and \
                    not activity.shared_activity.props.private:
                self.private.props.sensitive = False
                self.neighborhood.props.sensitive = False
                self.neighborhood.props.active = True
            else:
                self.private.props.sensitive = True
                self.neighborhood.props.sensitive = True
                self.private.props.active = True
        finally:
            self.neighborhood.handler_unblock(self._neighborhood_handle)


class TitleEntry(Gtk.ToolItem):

    def __init__(self, activity, **kwargs):
        Gtk.ToolItem.__init__(self)
        self.set_expand(False)

        self.entry = Gtk.Entry(**kwargs)
        self.entry.set_size_request(int(Gdk.Screen.width() / 3), -1)
        self.entry.set_text(activity.metadata['title'])
        self.entry.connect(
            'focus-out-event', self.__title_changed_cb, activity)
        self.entry.connect('button-press-event', self.__button_press_event_cb)
        self.entry.show()
        self.add(self.entry)

        activity.metadata.connect('updated', self.__jobject_updated_cb)
        activity.connect('_closing', self.__closing_cb)

    def modify_bg(self, state, color):
        Gtk.ToolItem.modify_bg(self, state, color)
        self.entry.modify_bg(state, color)

    def __jobject_updated_cb(self, jobject):
        if self.entry.has_focus():
            return
        if self.entry.get_text() == jobject['title']:
            return
        self.entry.set_text(jobject['title'])

    def __closing_cb(self, activity):
        self.save_title(activity)
        return False

    def __title_changed_cb(self, editable, event, activity):
        self.save_title(activity)
        return False

    def __button_press_event_cb(self, widget, event):
        if widget.is_focus():
            return False
        else:
            widget.grab_focus()
            widget.select_region(0, -1)
            return True

    def save_title(self, activity):
        title = self.entry.get_text()
        if title == activity.metadata['title']:
            return

        activity.metadata['title'] = title
        activity.metadata['title_set_by_user'] = '1'
        activity.save()

        activity.set_title(title)

        shared_activity = activity.get_shared_activity()
        if shared_activity is not None:
            shared_activity.props.name = title


class DescriptionItem(ToolButton):

    def __init__(self, activity, **kwargs):
        ToolButton.__init__(self, 'edit-description', **kwargs)
        self.set_tooltip(_('Description'))
        self.palette_invoker.props.toggle_palette = True
        self.palette_invoker.props.lock_palette = True
        self.props.hide_tooltip_on_click = False
        self._palette = self.get_palette()

        description_box = PaletteMenuBox()
        sw = Gtk.ScrolledWindow()
        sw.set_size_request(int(Gdk.Screen.width() / 2),
                            2 * style.GRID_CELL_SIZE)
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self._text_view = Gtk.TextView()
        self._text_view.set_left_margin(style.DEFAULT_PADDING)
        self._text_view.set_right_margin(style.DEFAULT_PADDING)
        self._text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        text_buffer = Gtk.TextBuffer()
        if 'description' in activity.metadata:
            text_buffer.set_text(activity.metadata['description'])
        self._text_view.set_buffer(text_buffer)
        self._text_view.connect('focus-out-event',
                                self.__description_changed_cb, activity)
        sw.add(self._text_view)
        description_box.append_item(sw, vertical_padding=0)
        self._palette.set_content(description_box)
        description_box.show_all()

        activity.metadata.connect('updated', self.__jobject_updated_cb)

    def set_expanded(self, expanded):
        box = self.toolbar_box
        if not box:
            return

        if not expanded:
            self.palette_invoker.notify_popdown()
            return

        if box.expanded_button is not None:
            box.expanded_button.queue_draw()
            if box.expanded_button != self:
                box.expanded_button.set_expanded(False)
        box.expanded_button = self

    def get_toolbar_box(self):
        parent = self.get_parent()
        if not hasattr(parent, 'owner'):
            return None
        return parent.owner

    toolbar_box = property(get_toolbar_box)

    def _get_text_from_buffer(self):
        buf = self._text_view.get_buffer()
        start_iter = buf.get_start_iter()
        end_iter = buf.get_end_iter()
        return buf.get_text(start_iter, end_iter, False)

    def __jobject_updated_cb(self, jobject):
        if self._text_view.has_focus():
            return
        if 'description' not in jobject:
            return
        if self._get_text_from_buffer() == jobject['description']:
            return
        buf = self._text_view.get_buffer()
        buf.set_text(jobject['description'])

    def __description_changed_cb(self, widget, event, activity):
        description = self._get_text_from_buffer()
        if 'description' in activity.metadata and \
                description == activity.metadata['description']:
            return

        activity.metadata['description'] = description
        activity.save()
        return False


class BulletinButton(ToggleToolButton):

    def __init__(self):
        ToggleToolButton.__init__(self, icon_name='computer-xo')

        self.set_tooltip("Bulletin Board")


class BulletinChatEntry(Gtk.ToolItem):
    def __init__(self, **kwargs):
        Gtk.ToolItem.__init__(self)
        self.set_expand(True)

        self.entry = Gtk.Entry(**kwargs)

        self.entry.show()
        self.add(self.entry)


class BulletinToolbar(Gtk.Toolbar):
    def __init__(self):
        GObject.GObject.__init__(self)

        entry = BulletinChatEntry()
        entry.show()
        self.insert(entry, -1)

        separator = Gtk.SeparatorToolItem()
        separator.props.draw = True
        separator.set_expand(False)
        separator.show()
        self.insert(separator, -1)

        self.show_all()

BORDER_DEFAULT = style.LINE_WIDTH


class MessageBox(Gtk.HBox):
    def __init__(self, **kwargs):
        GObject.GObject.__init__(self, **kwargs)

        self._radius = style.zoom(10)
        self.border_color = style.Color("#0000FF")
        self.background_color = style.Color("#FFFF00")

        self.set_resize_mode(Gtk.ResizeMode.PARENT)
        self.connect("draw", self.__draw_cb)
        self.connect("add", self.__add_cb)

        self.close_button = ToolButton(icon_name='entry-stop')
        self.pack_end(self.close_button, False, False, 0)

        self.close_button.connect("clicked", self._close_box)

    def _close_box(self, button):
        self.get_parent().remove(self)

    def __add_cb(self, widget, params):
        child.set_border_width(style.zoom(5))

    def __draw_cb(self, widget, cr):

        rect = self.get_allocation()
        x = rect.x
        y = rect.y
        logging.debug("width = " + str(rect.width))

        width = rect.width - BORDER_DEFAULT
        height = rect.height - BORDER_DEFAULT

        cr.move_to(x, y)
        cr.arc(x + width - self._radius, y + self._radius,
                            self._radius, math.pi * 1.5, math.pi * 2)
        cr.arc(x + width - self._radius, y + height - self._radius,
                            self._radius, 0, math.pi * 0.5)
        cr.arc(x + self._radius, y + height - self._radius,
                            self._radius, math.pi * 0.5, math.pi)
        cr.arc(x + self._radius, y + self._radius, self._radius,
                            math.pi, math.pi * 1.5)
        cr.close_path()

        if self.background_color is not None:
            r, g, b, __ = self.background_color.get_rgba()
            cr.set_source_rgb(r, g, b)
            cr.fill_preserve()

        if self.border_color is not None:
            r, g, b, __ = self.border_color.get_rgba()
            cr.set_source_rgb(r, g, b)
            cr.set_line_width(BORDER_DEFAULT)
            cr.stroke()

        return False

_URL_REGEXP = re.compile('((http|ftp)s?://)?'
               '(([-a-zA-Z0-9]+[.])+[-a-zA-Z0-9]{2,}|([0-9]{1,3}[.]){3}[0-9]{1,3})'
                '(:[1-9][0-9]{0,4})?(/[-a-zA-Z0-9/%~@&_+=;:,.?#]*[a-zA-Z0-9/])?')


class TextBox(Gtk.TextView):

    hand_cursor = Gdk.Cursor.new(Gdk.CursorType.HAND2)

    def __init__(self, color, bg_color, lang_rtl=False):

        self._lang_rtl = lang_rtl
        GObject.GObject.__init__(self)
        self.set_editable(False)
        self.set_cursor_visible(False)

        self.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.get_buffer().set_text("", 0)

        self.iter_text = self.get_buffer().get_iter_at_offset(0)

        self.fg_tag = self.get_buffer().create_tag("foreground_color",
            foreground=color.get_html())

        self.bold_tag = self.get_buffer().create_tag("bold",
            weight = Pango.Weight.BOLD)

        self._subscript_tag = self.get_buffer().create_tag('subscript',
            rise=-7 * Pango.SCALE)

        self.modify_bg(0, bg_color.get_gdk_color())

    def add_text(self, text):
        buf = self.get_buffer()
        #buf.insert(self.iter_text, '\n')

        words = text.split()
        for word in words:
            if _URL_REGEXP.match(word) is not None:
                tag = buf.create_tag(None,
                    foreground="blue", underline=Pango.Underline.SINGLE)
                tag.set_data("url", word)
                palette = _URLMenu(word)

                tag.set_data('palette', palette)
                buf.insert_with_tags(self.iter_text, word, tag,
                    self.fg_tag, self.bold_tag)
            else:
                buf.insert_with_tags(self.iter_text, word, self.fg_tag, self.bold_tag)
                buf.insert_with_tags(self.iter_text, ' ', self.fg_tag)


class ColorLabel(Gtk.Label):
    def __init__(self, text, color=None):

            GObject.GObject.__init__(self)
            self.set_use_markup(True)
            self._color = color
            if self._color is not None:
                text = '<span foreground="%s">%s</span>' % (self._color.get_html(), text)
            self.set_markup(text)


class BulletinBoard():
    def __init__(self, activity):

        self.left = self._create_left_panel()

        self.right = self._create_right_panel()

        self.mb = MessageBox()

        s = Gdk.Screen.get_default()
        width = s.get_width()
        height = s.get_height()

        self.fixed = Gtk.Fixed()

        self.button = BulletinButton()
        self.button.connect("clicked", self._toggle)

        self.box_button = ToolbarButton()
        self.box_button.props.icon_name = 'computer'

        self.toolbar = BulletinToolbar()
        self.box_button.props.page = self.toolbar

        name = ColorLabel(text = "native :", color = style.Color("#000080"))
        self.name_v = Gtk.VBox()
        self.name_v.pack_start(name, False, False, 0)

        self.mb.pack_start(self.name_v, False, False, 0)

        msg = TextBox(style.Color("#171ED2"), style.Color("#D21717"))
        msg.add_text("Hi, This is my first message !!!")
        #msg.set_size_request(250,10)
        self.vb = Gtk.VBox()
        #self.vb.set_size_request(300, 40)
        self.vb.pack_start(msg, True, True, 0)

        self.mb.pack_start(self.vb, True, True, 0)
        logging.debug("low = " + str(width) + "high = " + str(height))
        x = random.randint(30, width)
        y = random.randint(1, height)
        self.fixed.put(self.mb, x, y)

    def _toggle(self, button):

        if button.get_active():
                self.left.show()
                self.right.show()
                self.name_v.show()
                self.vb.show()
                self.mb.show()
                self.fixed.show_all()
                self.box_button.show()
        else:
                self.left.hide()
                self.right.hide()
                self.vb.hide()
                self.name_v.hide()
                self.mb.hide()
                self.fixed.hide()
                self.box_button.hide()

    def _create_left_panel(self):

        panel = self._create_panel(Gtk.PositionType.LEFT)

        tray = ClipboardTray()
        panel.append(tray)
        tray.show()

        return panel

    def _create_right_panel(self):

        panel = self._create_panel(Gtk.PositionType.RIGHT)

        tray = FriendsTray()
        panel.append(tray)
        tray.show()

        return panel

    def _create_panel(self, orientation):

        panel = FrameWindow(orientation)
        return panel


class ActivityToolbar(Gtk.Toolbar):
    """The Activity toolbar with the Journal entry title and sharing button"""

    def __init__(self, activity, orientation_left=False):
        Gtk.Toolbar.__init__(self)

        self._activity = activity

        if activity.metadata:
            title_button = TitleEntry(activity)
            title_button.show()
            self.insert(title_button, -1)
            self.title = title_button.entry

        if not orientation_left:
            separator = Gtk.SeparatorToolItem()
            separator.props.draw = False
            separator.set_expand(True)
            self.insert(separator, -1)
            separator.show()

        if activity.metadata:
            description_item = DescriptionItem(activity)
            description_item.show()
            self.insert(description_item, -1)

        self.share = ShareButton(activity)
        self.share.show()
        self.insert(self.share, -1)


class EditToolbar(Gtk.Toolbar):
    """Provides the standard edit toolbar for Activities.

    Members:
        undo  -- the undo button
        redo  -- the redo button
        copy  -- the copy button
        paste -- the paste button
        separator -- A separator between undo/redo and copy/paste

    This class only provides the 'edit' buttons in a standard layout,
    your activity will need to either hide buttons which make no sense for your
    Activity, or you need to connect the button events to your own callbacks:

        ## Example from Read.activity:
        # Create the edit toolbar:
        self._edit_toolbar = EditToolbar(self._view)
        # Hide undo and redo, they're not needed
        self._edit_toolbar.undo.props.visible = False
        self._edit_toolbar.redo.props.visible = False
        # Hide the separator too:
        self._edit_toolbar.separator.props.visible = False

        # As long as nothing is selected, copy needs to be insensitive:
        self._edit_toolbar.copy.set_sensitive(False)
        # When the user clicks the button, call _edit_toolbar_copy_cb()
        self._edit_toolbar.copy.connect('clicked', self._edit_toolbar_copy_cb)

        # Add the edit toolbar:
        toolbox.add_toolbar(_('Edit'), self._edit_toolbar)
        # And make it visible:
        self._edit_toolbar.show()
    """

    def __init__(self):
        Gtk.Toolbar.__init__(self)

        self.undo = UndoButton()
        self.insert(self.undo, -1)
        self.undo.show()

        self.redo = RedoButton()
        self.insert(self.redo, -1)
        self.redo.show()

        self.separator = Gtk.SeparatorToolItem()
        self.separator.set_draw(True)
        self.insert(self.separator, -1)
        self.separator.show()

        self.copy = CopyButton()
        self.insert(self.copy, -1)
        self.copy.show()

        self.paste = PasteButton()
        self.insert(self.paste, -1)
        self.paste.show()
