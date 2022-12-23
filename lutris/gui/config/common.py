"""Shared config dialog stuff"""
# pylint: disable=not-an-iterable
import os
from gettext import gettext as _

from gi.repository import Gtk, Pango

from lutris import runners, settings
from lutris.config import LutrisConfig, make_game_config_id
from lutris.game import Game
from lutris.gui import dialogs
from lutris.gui.config import DIALOG_HEIGHT, DIALOG_WIDTH
from lutris.gui.config.boxes import GameBox, RunnerBox, SystemBox
from lutris.gui.dialogs import DirectoryDialog, ErrorDialog, ModelessDialog, QuestionDialog
from lutris.gui.widgets.common import Label, NumberEntry, SlugEntry, VBox
from lutris.gui.widgets.notifications import send_notification
from lutris.gui.widgets.utils import get_pixbuf
from lutris.runners import import_runner
from lutris.services.lutris import LutrisBanner, LutrisCoverart, LutrisIcon, download_lutris_media
from lutris.util.log import logger
from lutris.util.strings import slugify
from lutris.runners.runner import Runner


# pylint: disable=too-many-instance-attributes, no-member
class GameDialogCommon(ModelessDialog, Runner.DialogInstallUIDelegate):
    """Base class for config dialogs"""
    no_runner_label = _("Select a runner in the Game Info tab")

    def __init__(self, title, parent=None, use_header_bar=True):
        super().__init__(title, parent=parent, border_width=0, use_header_bar=use_header_bar)
        self.set_default_size(DIALOG_WIDTH, DIALOG_HEIGHT)
        self.vbox.set_border_width(0)

        self.notebook = None
        self.name_entry = None
        self.runner_box = None

        self.timer_id = None
        self.game = None
        self.saved = None
        self.slug = None
        self.slug_entry = None
        self.directory_entry = None
        self.year_entry = None
        self.slug_change_button = None
        self.runner_dropdown = None
        self.image_buttons = {}
        self.option_page_indices = set()
        self.advanced_switch = None
        self.game_box = None
        self.system_box = None
        self.runner_name = None
        self.runner_index = None
        self.lutris_config = None
        self.service_medias = {"icon": LutrisIcon(), "banner": LutrisBanner(), "coverart_big": LutrisCoverart()}

        self.accelerators = Gtk.AccelGroup()
        self.add_accel_group(self.accelerators)

        self.connect("response", self.on_response)

    @staticmethod
    def build_scrolled_window(widget):
        """Return a scrolled window containing config widgets"""
        scrolled_window = Gtk.ScrolledWindow(visible=True)
        scrolled_window.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled_window.add(widget)
        return scrolled_window

    def build_notebook(self):
        self.notebook = Gtk.Notebook(visible=True)
        self.notebook.set_show_border(False)
        self.notebook.connect("switch-page", lambda _n, _p, index:
                              self.update_advanced_switch_visibilty(index))
        self.vbox.pack_start(self.notebook, True, True, 0)

    def build_tabs(self, config_level):
        """Build tabs (for game and runner levels)"""
        self.timer_id = None
        if config_level == "game":
            self._build_info_tab()
            self._build_game_tab()
        self._build_runner_tab(config_level)
        self._build_system_tab(config_level)
        self.update_advanced_switch_visibilty(self.notebook.get_current_page())

    def update_advanced_switch_visibilty(self, current_page_index):
        if self.advanced_switch and self.notebook:
            show_switch = current_page_index in self.option_page_indices
            self.advanced_switch.set_visible(show_switch)

    def _build_info_tab(self):
        info_box = VBox()

        if self.game:
            info_box.pack_start(self._get_banner_box(), False, False, 6)  # Banner

        info_box.pack_start(self._get_name_box(), False, False, 6)  # Game name

        self.runner_box = self._get_runner_box()
        info_box.pack_start(self.runner_box, False, False, 6)  # Runner

        info_box.pack_start(self._get_year_box(), False, False, 6)  # Year

        if self.game:
            info_box.pack_start(self._get_slug_box(), False, False, 6)
            info_box.pack_start(self._get_directory_box(), False, False, 6)
            info_box.pack_start(self._get_launch_config_box(), False, False, 6)

        info_sw = self.build_scrolled_window(info_box)
        self._add_notebook_tab(info_sw, _("Game info"))

    def _get_name_box(self):
        box = Gtk.Box(spacing=12, margin_right=12, margin_left=12)
        label = Label(_("Name"))
        box.pack_start(label, False, False, 0)
        self.name_entry = Gtk.Entry()
        self.name_entry.set_max_length(150)
        if self.game:
            self.name_entry.set_text(self.game.name)
        box.pack_start(self.name_entry, True, True, 0)
        return box

    def _get_slug_box(self):
        slug_box = Gtk.Box(spacing=12, margin_right=12, margin_left=12)

        label = Label(_("Identifier"))
        slug_box.pack_start(label, False, False, 0)

        self.slug_entry = SlugEntry()
        self.slug_entry.set_text(self.game.slug)
        self.slug_entry.set_sensitive(False)
        self.slug_entry.connect("activate", self.on_slug_entry_activate)
        slug_box.pack_start(self.slug_entry, True, True, 0)

        self.slug_change_button = Gtk.Button(_("Change"))
        self.slug_change_button.connect("clicked", self.on_slug_change_clicked)
        slug_box.pack_start(self.slug_change_button, False, False, 0)

        return slug_box

    def _get_directory_box(self):
        """Return widget displaying the location of the game and allowing to move it"""
        box = Gtk.Box(spacing=12, margin_right=12, margin_left=12, visible=True)
        label = Label(_("Directory"))
        box.pack_start(label, False, False, 0)
        self.directory_entry = Gtk.Entry(visible=True)
        self.directory_entry.set_text(self.game.directory)
        self.directory_entry.set_sensitive(False)
        box.pack_start(self.directory_entry, True, True, 0)
        move_button = Gtk.Button(_("Move"), visible=True)
        move_button.connect("clicked", self.on_move_clicked)
        box.pack_start(move_button, False, False, 0)
        return box

    def _get_launch_config_box(self):
        box = Gtk.Box(spacing=12, margin_right=12, margin_left=12, visible=True)

        game_config = self.game.config.game_level.get("game", {})
        preferred_name = game_config.get("preferred_launch_config_name")

        if preferred_name:
            spacer = Gtk.Box()
            spacer.set_size_request(230, -1)
            box.pack_start(spacer, False, False, 0)

            if preferred_name == Game.PRIMARY_LAUNCH_CONFIG_NAME:
                text = _("The default launch option will be used for this game")
            else:
                text = _("The '%s' launch option will be used for this game") % preferred_name
            label = Gtk.Label(text)
            label.set_line_wrap(True)
            label.set_halign(Gtk.Align.START)
            label.set_xalign(0.0)
            label.set_valign(Gtk.Align.CENTER)
            box.pack_start(label, True, True, 0)
            button = Gtk.Button(_("Reset"))
            button.connect("clicked", self.on_reset_preferred_launch_config_clicked, box)
            button.set_valign(Gtk.Align.CENTER)
            box.pack_start(button, False, False, 0)
        else:
            box.hide()
        return box

    def on_reset_preferred_launch_config_clicked(self, _button, launch_config_box):
        game_config = self.game.config.game_level.get("game", {})
        game_config.pop("preferred_launch_config_name", None)
        game_config.pop("preferred_launch_config_index", None)
        launch_config_box.hide()

    def _get_runner_box(self):
        runner_box = Gtk.Box(spacing=12, margin_right=12, margin_left=12)

        runner_label = Label(_("Runner"))
        runner_box.pack_start(runner_label, False, False, 0)

        self.runner_dropdown = self._get_runner_dropdown()
        runner_box.pack_start(self.runner_dropdown, True, True, 0)

        return runner_box

    def _get_banner_box(self):
        banner_box = Gtk.Box(spacing=12, margin_right=12, margin_left=12)

        label = Label("")
        banner_box.pack_start(label, False, False, 0)

        self._create_image_button(banner_box, "coverart_big", _("Set custom cover art"), _("Remove custom cover art"))
        self._create_image_button(banner_box, "banner", _("Set custom banner"), _("Remove custom banner"))
        self._create_image_button(banner_box, "icon", _("Set custom icon"), _("Remove custom icon"))

        return banner_box

    def _create_image_button(self, banner_box, image_type, image_tooltip, reset_tooltip):
        """This adds an image button and its reset button to the box given,
        and adds the image button to self.image_buttons for future reference."""
        image_button = Gtk.Button()
        self._set_image(image_type, image_button)
        image_button.set_tooltip_text(image_tooltip)
        image_button.connect("clicked", self.on_custom_image_select, image_type)
        image_button.set_valign(Gtk.Align.CENTER)
        banner_box.pack_start(image_button, False, False, 0)

        reset_button = Gtk.Button.new_from_icon_name("edit-clear", Gtk.IconSize.MENU)
        reset_button.set_relief(Gtk.ReliefStyle.NONE)
        reset_button.set_tooltip_text(reset_tooltip)
        reset_button.connect("clicked", self.on_custom_image_reset_clicked, image_type)
        reset_button.set_valign(Gtk.Align.CENTER)
        banner_box.pack_start(reset_button, False, False, 0)
        self.image_buttons[image_type] = image_button

    def _get_year_box(self):
        box = Gtk.Box(spacing=12, margin_right=12, margin_left=12)

        label = Label(_("Release year"))
        box.pack_start(label, False, False, 0)
        self.year_entry = NumberEntry()
        self.year_entry.set_max_length(10)
        if self.game:
            self.year_entry.set_text(str(self.game.year or ""))
        box.pack_start(self.year_entry, True, True, 0)

        return box

    def _set_image(self, image_format, image_button):
        service_media = self.service_medias[image_format]
        image = Gtk.Image()
        game_slug = self.slug or (self.game.slug if self.game else "")

        pixbuf = service_media.get_pixbuf_for_game(game_slug, service_media.config_ui_size)
        image.set_from_pixbuf(pixbuf)
        image_button.set_image(image)

    def _get_runner_dropdown(self):
        runner_liststore = self._get_runner_liststore()
        runner_dropdown = Gtk.ComboBox.new_with_model(runner_liststore)
        runner_dropdown.set_id_column(1)
        runner_index = 0
        if self.runner_name:
            for runner in runner_liststore:
                if self.runner_name == str(runner[1]):
                    break
                runner_index += 1
        self.runner_index = runner_index
        runner_dropdown.set_active(self.runner_index)
        runner_dropdown.connect("changed", self.on_runner_changed)
        cell = Gtk.CellRendererText()
        cell.props.ellipsize = Pango.EllipsizeMode.END
        runner_dropdown.pack_start(cell, True)
        runner_dropdown.add_attribute(cell, "text", 0)
        return runner_dropdown

    @staticmethod
    def _get_runner_liststore():
        """Build a ListStore with available runners."""
        runner_liststore = Gtk.ListStore(str, str)
        runner_liststore.append((_("Select a runner from the list"), ""))
        for runner in runners.get_installed():
            description = runner.description
            runner_liststore.append(("%s (%s)" % (runner.human_name, description), runner.name))
        return runner_liststore

    def on_slug_change_clicked(self, widget):
        if self.slug_entry.get_sensitive() is False:
            widget.set_label(_("Apply"))
            self.slug_entry.set_sensitive(True)
        else:
            self.change_game_slug()

    def on_slug_entry_activate(self, _widget):
        self.change_game_slug()

    def change_game_slug(self):
        slug = self.slug_entry.get_text()
        download_lutris_media(slug)

        self.slug = slug
        for image_type, image_button in self.image_buttons.items():
            self._set_image(image_type, image_button)
        self.slug_entry.set_sensitive(False)
        self.slug_change_button.set_label(_("Change"))

    def on_move_clicked(self, _button):
        new_location = DirectoryDialog("Select new location for the game",
                                       default_path=self.game.directory, parent=self)
        if not new_location.folder or new_location.folder == self.game.directory:
            return
        move_dialog = dialogs.MoveDialog(self.game, new_location.folder, parent=self)
        move_dialog.connect("game-moved", self.on_game_moved)
        move_dialog.move()

    def on_game_moved(self, dialog):
        """Show a notification when the game is moved"""
        new_directory = dialog.new_directory
        if new_directory:
            self.directory_entry.set_text(new_directory)
            send_notification("Finished moving game", "%s moved to %s" % (dialog.game, new_directory))
        else:
            send_notification("Failed to move game", "Lutris could not move %s" % dialog.game)

    def _build_game_tab(self):
        if self.game and self.runner_name:
            self.game.runner_name = self.runner_name
            if not self.game.runner or self.game.runner.name != self.runner_name:
                try:
                    self.game.runner = runners.import_runner(self.runner_name)()
                except runners.InvalidRunner:
                    pass
            self.game_box = GameBox(self.lutris_config, self.game)
            game_sw = self.build_scrolled_window(self.game_box)
        elif self.runner_name:
            game = Game(None)
            game.runner_name = self.runner_name
            self.game_box = GameBox(self.lutris_config, game)
            game_sw = self.build_scrolled_window(self.game_box)
        else:
            game_sw = Gtk.Label(label=self.no_runner_label)
        self._add_notebook_tab(game_sw, _("Game options"))

    def _build_runner_tab(self, _config_level):
        if self.runner_name:
            self.runner_box = RunnerBox(self.lutris_config, self.game)
            runner_sw = self.build_scrolled_window(self.runner_box)
        else:
            runner_sw = Gtk.Label(label=self.no_runner_label)
        page_index = self._add_notebook_tab(runner_sw, _("Runner options"))
        self.option_page_indices.add(page_index)

    def _build_system_tab(self, _config_level):
        if not self.lutris_config:
            raise RuntimeError("Lutris config not loaded yet")
        self.system_box = SystemBox(self.lutris_config)
        page_index = self._add_notebook_tab(
            self.build_scrolled_window(self.system_box),
            _("System options")
        )
        self.option_page_indices.add(page_index)

    def _add_notebook_tab(self, widget, label):
        return self.notebook.append_page(widget, Gtk.Label(label=label))

    def build_action_area(self, button_callback):
        self.action_area.set_layout(Gtk.ButtonBoxStyle.END)
        self.action_area.set_border_width(10)

        # Buttons
        cancel_button = self.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        cancel_button.set_valign(Gtk.Align.CENTER)

        save_button = self.add_styled_button(_("Save"), Gtk.ResponseType.NONE, css_class="suggested-action")
        save_button.set_valign(Gtk.Align.CENTER)
        save_button.connect("clicked", button_callback)

        key, mod = Gtk.accelerator_parse("<Control>s")
        save_button.add_accelerator("clicked", self.accelerators, key, mod, Gtk.AccelFlags.VISIBLE)

        # Advanced settings toggle

        if self.props.use_header_bar:
            switch_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                                 spacing=5,
                                 no_show_all=True)
            switch_box.set_tooltip_text(_("Show advanced options"))

            switch_label = Gtk.Label(_("Advanced"), visible=True)
            switch = Gtk.Switch(visible=True)
            switch.set_state(settings.read_setting("show_advanced_options") == "True")
            switch.connect("state_set", lambda _w, s:
                           self.on_show_advanced_options_toggled(bool(s)))

            switch_box.pack_start(switch_label, False, False, 0)
            switch_box.pack_end(switch, False, False, 0)

            header_bar = self.get_header_bar()
            header_bar.pack_end(switch_box)

            self.advanced_switch = switch_box
            self.update_advanced_switch_visibilty(self.notebook.get_current_page())
        else:
            checkbox = Gtk.CheckButton(label=_("Show advanced options"))
            checkbox.set_active(settings.read_setting("show_advanced_options") == "True")
            checkbox.connect("toggled", lambda *x:
                             self.on_show_advanced_options_toggled(bool(checkbox.get_active())))
            checkbox.set_halign(Gtk.Align.START)
            self.action_area.pack_start(checkbox, True, True, 0)
            self.action_area.set_child_secondary(checkbox, True)

    def on_show_advanced_options_toggled(self, is_active):
        settings.write_setting("show_advanced_options", is_active)

        self._set_advanced_options_visible(is_active)

    def _set_advanced_options_visible(self, value):
        """Change visibility of advanced options across all config tabs."""
        widgets = self.system_box.get_children()
        if self.runner_name:
            widgets += self.runner_box.get_children()
        if self.game:
            widgets += self.game_box.get_children()

        for widget in widgets:
            if widget.get_style_context().has_class("advanced"):
                widget.set_visible(value)
                if value:
                    widget.set_no_show_all(not value)
                    widget.show_all()

    def on_runner_changed(self, widget):
        """Action called when runner drop down is changed."""
        new_runner_index = widget.get_active()
        if self.runner_index and new_runner_index != self.runner_index:
            dlg = QuestionDialog(
                {
                    "parent": self,
                    "question":
                    _("Are you sure you want to change the runner for this game ? "
                      "This will reset the full configuration for this game and "
                      "is not reversible."),
                    "title":
                    _("Confirm runner change"),
                }
            )

            if dlg.result == Gtk.ResponseType.YES:
                self.runner_index = new_runner_index
                self._switch_runner(widget)
            else:
                # Revert the dropdown menu to the previously selected runner
                widget.set_active(self.runner_index)
        else:
            self.runner_index = new_runner_index
            self._switch_runner(widget)

    def _switch_runner(self, widget):
        """Rebuilds the UI on runner change"""
        current_page = self.notebook.get_current_page()
        if self.runner_index == 0:
            logger.info("No runner selected, resetting configuration")
            self.runner_name = None
            self.lutris_config = None
        else:
            runner_name = widget.get_model()[self.runner_index][1]
            if runner_name == self.runner_name:
                logger.debug("Runner unchanged, not creating a new config")
                return
            logger.info("Creating new configuration with runner %s", runner_name)
            self.runner_name = runner_name
            self.lutris_config = LutrisConfig(runner_slug=self.runner_name, level="game")
        self._rebuild_tabs()
        self.notebook.set_current_page(current_page)

    def _rebuild_tabs(self):
        for i in range(self.notebook.get_n_pages(), 1, -1):
            self.notebook.remove_page(i - 1)
        self.option_page_indices.clear()
        self._build_game_tab()
        self._build_runner_tab("game")
        self._build_system_tab("game")
        self.show_all()

    def on_response(self, _widget, response):
        if response in (Gtk.ResponseType.CANCEL, response == Gtk.ResponseType.DELETE_EVENT):
            # Reload the config to clean out any changes we may have made
            if self.game:
                self.game.load_config()
        if response != Gtk.ResponseType.NONE:
            self.destroy()

    def is_valid(self):
        if not self.runner_name:
            ErrorDialog(_("Runner not provided"), parent=self)
            return False
        if not self.name_entry.get_text():
            ErrorDialog(_("Please fill in the name"), parent=self)
            return False
        if self.runner_name == "steam" and not self.lutris_config.game_config.get("appid"):
            ErrorDialog(_("Steam AppID not provided"), parent=self)
            return False
        invalid_fields = []
        runner_class = import_runner(self.runner_name)
        runner_instance = runner_class()
        for config in ["game", "runner"]:
            for k, v in getattr(self.lutris_config, config + "_config").items():
                option = runner_instance.find_option(config + "_options", k)
                if option is None:
                    continue
                validator = option.get("validator")
                if validator is not None:
                    try:
                        res = validator(v)
                        logger.debug("%s validated successfully: %s", k, res)
                    except Exception:
                        invalid_fields.append(option.get("label"))
        if invalid_fields:
            ErrorDialog(_("The following fields have invalid values: ") + ", ".join(invalid_fields), parent=self)
            return False
        return True

    def on_save(self, _button):
        """Save game info and destroy widget. Return True if success."""
        if not self.is_valid():
            logger.warning(_("Current configuration is not valid, ignoring save request"))
            return False
        name = self.name_entry.get_text()

        if not self.slug:
            self.slug = slugify(name)

        if not self.game:
            self.game = Game()

        year = None
        if self.year_entry.get_text():
            year = int(self.year_entry.get_text())

        if not self.lutris_config.game_config_id:
            self.lutris_config.game_config_id = make_game_config_id(self.slug)

        runner_class = runners.import_runner(self.runner_name)
        runner = runner_class(self.lutris_config)

        # extract icon for wine games
        if self.runner_name == "wine" and "icon" not in self.game.custom_images:
            runner.extract_icon_exe(self.slug)

        self.game.name = name
        self.game.slug = self.slug
        self.game.year = year
        self.game.game_config_id = self.lutris_config.game_config_id
        self.game.runner = runner
        self.game.runner_name = self.runner_name
        self.game.is_installed = True
        self.game.config = self.lutris_config
        self.game.save(save_config=True)
        self.destroy()
        self.saved = True
        return True

    def on_custom_image_select(self, _widget, image_type):
        dialog = Gtk.FileChooserNative.new(
            _("Please choose a custom image"),
            self,
            Gtk.FileChooserAction.OPEN,
            None,
            None,
        )

        image_filter = Gtk.FileFilter()
        image_filter.set_name(_("Images"))
        image_filter.add_pixbuf_formats()
        dialog.add_filter(image_filter)

        response = dialog.run()
        if response == Gtk.ResponseType.ACCEPT:
            slug = self.slug or self.game.slug
            image_path = dialog.get_filename()
            service_media = self.service_medias[image_type]
            self.game.custom_images.add(image_type)
            dest_path = service_media.get_absolute_path(slug)
            file_format = service_media.file_format
            size = service_media.custom_media_storage_size
            pixbuf = get_pixbuf(image_path, size)
            # JPEG encoding looks rather better at high quality;
            # PNG encoding just ignores this option.
            pixbuf.savev(dest_path, file_format, ["quality"], ["100"])
            self._set_image(image_type, self.image_buttons[image_type])
            service_media.update_desktop()

        dialog.destroy()

    def on_custom_image_reset_clicked(self, _widget, image_type):
        slug = self.slug or self.game.slug
        service_media = self.service_medias[image_type]
        dest_path = service_media.get_absolute_path(slug)
        self.game.custom_images.discard(image_type)
        if os.path.isfile(dest_path):
            os.remove(dest_path)
        download_lutris_media(self.game.slug)
        self._set_image(image_type, self.image_buttons[image_type])
