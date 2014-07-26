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
from sugar3.presence import presenceservice
from sugar3.activity import activity

from sugar3 import mime
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

        self.set_tooltip(_("Bulletin Board"))


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

        self.toolitems = BulletinChatEntry()
        self.toolitems.show()

        self.insert(self.toolitems, -1)

        separator = Gtk.SeparatorToolItem()
        separator.props.draw = True
        separator.set_expand(False)
        separator.show()
        self.insert(separator, -1)

        self.show_all()

BORDER_DEFAULT = 3 * style.LINE_WIDTH


class MessageBox(Gtk.HBox):
    def __init__(self, **kwargs):
        GObject.GObject.__init__(self, **kwargs)

        self._radius = style.zoom(20)
        self.border_color = style.Color("#0000FF")
        self.background_color = style.Color("#FFFF00")

        self.modify_bg(0, self.background_color.get_gdk_color())

        self.set_resize_mode(Gtk.ResizeMode.PARENT)
        self.connect("draw", self.__draw_cb)
        self.connect("add", self.__add_cb)

        close_icon = Icon(icon_name = 'entry-stop')
        close_icon.props.pixel_size = style.zoom(20)

        drag_icon = Icon(icon_name = 'hand1')
        drag_icon.props.pixel_size = style.zoom(20)

        self.drag_button = Gtk.Button()
        #self.drag_button.set_icon_widget(drag_icon)
        self.drag_button.set_image(drag_icon)
        drag_icon.show()
        self.drag_button.add_events(Gdk.EventMask.POINTER_MOTION_HINT_MASK | \
                              Gdk.EventMask.POINTER_MOTION_MASK)
        self.drag_button.connect("motion_notify_event", self.__motion_notify_cb)
        self.drag_button.connect("enter_notify_event", self.__enter_notify_cb)
        self.drag_button.connect("button-press-event", self._button_pressed)
        self.drag_button.connect("button-release-event", self._button_released)

        self.close_button = ToolButton(icon_name='entry-stop')
        self.close_button.set_icon_widget(close_icon)
        close_icon.show()
        self.close_button.connect("clicked", self._close_box)
        self.pack_end(self.close_button, False, False, 0)
        self.pack_start(self.drag_button, False, False, style.zoom(20))

    def __motion_notify_cb(self, widget, event):
        if event.get_state() & Gdk.ModifierType.BUTTON1_MASK:
            x, y = event.x, event.y
            ev = widget.get_parent().get_parent()
            fixed = ev.get_parent()
            self.lx = self.x + x - self.sx
            self.ly = self.y + y - self.sy
            fixed.move(ev, self.lx, self.ly)
            self.x, self.y = self.lx, self.ly

    def __enter_notify_cb(self, widget, event):
        win = widget.get_window()
        hand_cursor = Gdk.Cursor.new(Gdk.CursorType.HAND2)
        win.set_cursor(hand_cursor)

    def _button_pressed(self, widget, event):
        self.sx = event.x
        self.sy = event.y

    def _button_released(self, widget, event):
        self.x = self.lx
        self.y = self.ly

    def _close_box(self, button):
        self.get_parent().remove(self)

    def __add_cb(self, widget, params):
        child.set_border_width(style.zoom(5))

    def __draw_cb(self, widget, cr):

        rect = self.get_allocation()
        x = rect.x
        y = rect.y

        width = rect.width - BORDER_DEFAULT
        height = rect.height - BORDER_DEFAULT

        logging.debug("final x = " + str(self.x + rect.width) + "screen width = " + str(Gdk.Screen.width()))

        diff1 = self.x + rect.width - int(Gdk.Screen.width())
        diff2 = self.y + rect.height - int(Gdk.Screen.height())
        if diff1 >= 0 or diff2 >= 0:
            ev = self.get_parent()
            fixed = ev.get_parent()
            self.x = random.randint(self.panel_width, int(Gdk.Screen.width()) - rect.width - self.panel_width)
            self.y = random.randint(self.panel_width, int(Gdk.Screen.width()) - rect.height - self.panel_width)
            fixed.move(ev, self.x, self.y)

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
            weight=Pango.Weight.BOLD)

        self._subscript_tag = self.get_buffer().create_tag('subscript',
            rise=-7 * Pango.SCALE)

        if bg_color is not None:
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
                text = '<span foreground="%s">%s </span>' % (self._color.get_html(), text)
            self.set_markup(text)


class BulletinBoard():
    def __init__(self, cactivity):

        self._activity = cactivity

        self.is_active = False

        self.left = self._create_left_panel()

        self.right = self._create_right_panel()

        self.text_channel = None

        self.fixed = Gtk.Fixed()

        #self.button = BulletinButton()
        #self.button.connect("clicked", self._toggle)

        self.button = ToolbarButton()
        self.button.connect("clicked", self._toggle)
        self.button.props.icon_name = 'computer-xo'

        self.toolbar = BulletinToolbar()
        self.button.props.page = self.toolbar
        self.toolbar.toolitems.entry.connect('activate', self.entry_activate_cb)

        #self.share_button = ShareButton(self._activity)
        #self.share_button.private.props.active = False

        pserv = presenceservice.get_instance()
        self.owner = pserv.get_owner()

        # If any shared activity exists
        if self._activity.shared_activity:
            self._activity.connect('joined', self._joined_cb)  # joining an activity

            if self._activity.get_shared():  # already joined the activity
                self._joined_cb(self._activity)
        else:
            if not self._activity.metadata or (self._activity.metadata.get('share-scope',
                    activity.SCOPE_PRIVATE) == activity.SCOPE_PRIVATE):
                self._alert(_('Off-line'), _('Share, or invite someone.'))
            self._activity.connect('shared', self._shared_cb)

    def add_text(self, buddy, text):

        if not buddy:
            buddy = self.owner

        if type(buddy) is dict:
            nick = buddy['nick']
            color = buddy['color']
        else:
            nick = buddy.props.nick
            color = buddy.props.color

        try:
            color_stroke_html, color_fill_html = color.split(',')
        except ValueError:
            color_stroke_html, color_fill_html = ('#000000', '#888888')

        """ select box fill and stroke color"""

        color_stroke = style.Color(color_stroke_html)
        color_fill = style.Color(color_fill_html)

        """ select text color based on fill color """

        color_fill_rgba = style.Color(color_fill_html).get_rgba()
        color_fill_gray = (color_fill_rgba[0] + color_fill_rgba[1] +
            color_fill_rgba[2]) / 3

        """ black or white text color based on the intensity """

        if color_fill_gray < 0.5:
            text_color = style.COLOR_WHITE
        else:
            text_color = style.COLOR_BLACK

        """ Right To Left languages """

        if Pango.find_base_dir(nick, -1) == Pango.Direction.RTL:
            lang_rtl = True
        else:
            lang_rtl = False

        """  Generate Round Box with textbox and nick label """

        mb = MessageBox()  # OUTER ROUND BOX

        mb.background_color = color_fill
        mb.border_color = color_stroke

        name = ColorLabel(text=nick + " : ", color=text_color)
        name_v = Gtk.VBox()  # COLOR LABEL
        name_v.pack_start(name, False, False, style.zoom(10))

        mb.pack_start(name_v, False, False, style.zoom(10))

        msg = TextBox(text_color, color_fill, lang_rtl)  # TEXT BOX
        msg.add_text(text)

        """  Gtk.Fixed() container for ensuring fixed horizontal width """

        inner = Gtk.Fixed()
        inner.set_hexpand(False)
        inner.set_vexpand(True)
        inner.add(msg)

        if len(text) > int((Gdk.Screen.width() / 4) / 10):
            msg.set_size_request(int(Gdk.Screen.width() / 4), 30)
        else:
            msg.set_wrap_mode(Gtk.WrapMode.NONE)

        vb = Gtk.VBox()
        vb.pack_start(inner, False, False, style.zoom(10))

        mb.pack_start(vb, True, False, style.zoom(10))

        logging.debug('nick = ' + nick + 'text = ' + text)

        """ Place randomly on screen """

        mb.panel_width = style.GRID_CELL_SIZE + style.LINE_WIDTH
        mb.x = random.randint(mb.panel_width + style.LINE_WIDTH, Gdk.Screen.width() - mb.panel_width)
        mb.y = random.randint(mb.panel_width + style.LINE_WIDTH, Gdk.Screen.height() - mb.panel_width)

        ev = Gtk.EventBox()
        ev.add(mb)

        mb.show()

        logging.debug("x =" + str(mb.x) + "y= " + str(mb.y))
        self.fixed.put(ev, mb.x, mb.y)

        if self.is_active:
            self.fixed.show_all()

    def _setup(self):

        """ Setup Chat Client """

        logging.debug(" Chat setting up ")
        self.text_channel = TextChannelWrapper(
            self._activity.shared_activity.telepathy_text_chan,  # use text channel wrapper
            self._activity.shared_activity.telepathy_conn)

        self.text_channel.set_received_callback(self._received_cb)  # callback for received messaged

        self._alert(_('On-line'), _('Connected'))
        self._activity.shared_activity.connect('buddy-joined', self._buddy_joined_cb)
        self._activity.shared_activity.connect('buddy-left', self._buddy_left_cb)

        self.button.props.sensitive = True

    def _received_cb(self, buddy, text):
        if buddy:
            if type(buddy) is dict:
                nick = buddy['nick']
            else:
                nick = buddy.props.nick
        else:
            nick = "???"

        logging.debug("message received from - " + nick)

        self.add_text(buddy, text)

    def _shared_cb(self, sender):

        logging.debug("Activity Shared ! ")
        self._setup()

    def _joined_cb(self, sender):

        if not self._activity.shared_activity:  # Joined shared activity
            return

        logging.debug("Joined the session ")
        for buddy in self._activity.shared_activity.get_joined_buddies():
            self._buddy_already_exists(buddy)

        self._setup()

    def _buddy_already_exists(self, buddy):
        if buddy == self.owner:  # the user himself
            return

        self._alert(buddy.props.nick + ' ' + _('is here'))

    def _buddy_joined_cb(self, sender, buddy):
        if buddy == self.owner:  # display buddy who joined
            return

        self._alert(buddy.props.nick + ' ' + _('has joined'))

    def _buddy_left_cb(self, sender, buddy):
        if buddy == self.owner:  # display buddy who joined
            return

        self._alert(buddy.props.nick + ' ' + _('left'))

    def _toggle(self, button):

        if self.is_active is False:
                self.is_active = True
        else:
                self.is_active = False

        if self.is_active:
                self.left.show()
                self.right.show()
                self.fixed.show_all()
                #self.share_button.show()
                #self.box_button.show()
                #self.is_active = True
        else:
                self.left.hide()
                self.right.hide()
                self.fixed.hide()
                #self.box_button.hide()
                #self.share_button.hide()
                #self.is_active = False

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

    def _alert(self, title, text=None):
        alert = NotifyAlert(timeout=5)
        alert.props.title = title
        alert.props.msg = text
        self._activity.add_alert(alert)
        alert.connect('response', self._alert_cancel_cb)
        alert.show()

    def _alert_cancel_cb(self, alert, response_id):
        self._activity.remove_alert(alert)

    def entry_activate_cb(self, entry):

        text = entry.props.text
        logging.debug('Entry: ' + text)
        if text:
            self.add_text(self.owner, text)
            entry.props.text = ''
            if self.text_channel:
                self.text_channel.send(text)
            else:
                logging.debug('Failed to send message')


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

"""TextChannelWrapper | Source - Chat Activity """


class TextChannelWrapper(object):
    """Wrap a telepathy Text Channel to make usage simpler."""

    def __init__(self, text_chan, conn):
        """Connect to the text channel"""
        self._activity_cb = None
        self._activity_close_cb = None
        self._text_chan = text_chan
        self._conn = conn
        self._signal_matches = []
        m = self._text_chan[CHANNEL_INTERFACE].connect_to_signal(
            'Closed', self._closed_cb)
        self._signal_matches.append(m)

    def send(self, text):
        """Send text over the Telepathy text channel."""
        # XXX Implement CHANNEL_TEXT_MESSAGE_TYPE_ACTION
        if self._text_chan is not None:
            self._text_chan[CHANNEL_TYPE_TEXT].Send(
                CHANNEL_TEXT_MESSAGE_TYPE_NORMAL, text)

    def close(self):
        """Close the text channel."""
        logging.debug('Closing text channel')
        try:
            self._text_chan[CHANNEL_INTERFACE].Close()
        except Exception:
            logging.debug('Channel disappeared!')
            self._closed_cb()

    def _closed_cb(self):
        """Clean up text channel."""
        logging.debug('Text channel closed.')
        for match in self._signal_matches:
            match.remove()
        self._signal_matches = []
        self._text_chan = None
        if self._activity_close_cb is not None:
            self._activity_close_cb()

    def set_received_callback(self, callback):
        """Connect the function callback to the signal.

        callback -- callback function taking buddy and text args
        """
        if self._text_chan is None:
            return
        self._activity_cb = callback
        m = self._text_chan[CHANNEL_TYPE_TEXT].connect_to_signal('Received',
            self._received_cb)
        self._signal_matches.append(m)

    def handle_pending_messages(self):
        """Get pending messages and show them as received."""
        for identity, timestamp, sender, type_, flags, text in \
            self._text_chan[
                CHANNEL_TYPE_TEXT].ListPendingMessages(False):
            self._received_cb(identity, timestamp, sender, type_, flags, text)

    def _received_cb(self, identity, timestamp, sender, type_, flags, text):
        """Handle received text from the text channel.

        Converts sender to a Buddy.
        Calls self._activity_cb which is a callback to the activity.
        """
        if type_ != 0:
            # Exclude any auxiliary messages
            return

        if self._activity_cb:
            try:
                self._text_chan[CHANNEL_INTERFACE_GROUP]
            except Exception:
                # One to one XMPP chat
                nick = self._conn[
                    CONN_INTERFACE_ALIASING].RequestAliases([sender])[0]
                buddy = {'nick': nick, 'color': '#000000,#808080'}
            else:
                # Normal sugar3 MUC chat
                # XXX: cache these
                buddy = self._get_buddy(sender)
            self._activity_cb(buddy, text)
            self._text_chan[
                CHANNEL_TYPE_TEXT].AcknowledgePendingMessages([identity])
        else:
            logging.debug('Throwing received message on the floor'
                ' since there is no callback connected. See '
                'set_received_callback')

    def set_closed_callback(self, callback):
        """Connect a callback for when the text channel is closed.

        callback -- callback function taking no args

        """
        self._activity_close_cb = callback

    def _get_buddy(self, cs_handle):
        """Get a Buddy from a (possibly channel-specific) handle."""
        # XXX This will be made redundant once Presence Service
        # provides buddy resolution
        # Get the Presence Service
        pservice = presenceservice.get_instance()
        # Get the Telepathy Connection
        tp_name, tp_path = pservice.get_preferred_connection()
        conn = Connection(tp_name, tp_path)
        group = self._text_chan[CHANNEL_INTERFACE_GROUP]
        my_csh = group.GetSelfHandle()
        if my_csh == cs_handle:
            handle = conn.GetSelfHandle()
        elif group.GetGroupFlags() & \
               CHANNEL_GROUP_FLAG_CHANNEL_SPECIFIC_HANDLES:
            handle = group.GetHandleOwners([cs_handle])[0]
        else:
            handle = cs_handle

            # XXX: deal with failure to get the handle owner
            assert handle != 0

        return pservice.get_buddy_by_telepathy_handle(
            tp_name, tp_path, handle)
