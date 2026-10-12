import Adw from 'gi://Adw';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Gtk from 'gi://Gtk';

import {ExtensionPreferences} from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

const BUS_NAME = 'org.yogahinge';
const OBJ_PATH = '/org/yogahinge/Hinge';
const IFACE = 'org.yogahinge.Hinge1';

export default class YogaHingePrefs extends ExtensionPreferences {
    fillPreferencesWindow(window) {
        const settings = this.getSettings();
        window._settings = settings;

        const page = new Adw.PreferencesPage({
            title: 'Yoga Hinge',
            icon_name: 'computer-symbolic',
        });
        window.add(page);

        const status = new Adw.PreferencesGroup({title: 'Status'});
        page.add(status);
        const statusRow = new Adw.ActionRow({title: 'Hinge angle', subtitle: 'service not running'});
        status.add(statusRow);

        const group = new Adw.PreferencesGroup({
            title: 'Tablet mode',
            description: 'Tablet mode switches on when the hinge opens past the angle below and ' +
                'switches off again once it is below that angle minus the hysteresis. ' +
                '180° is the lid lying flat.',
        });
        page.add(group);

        const thresholdRow = new Adw.SpinRow({
            title: 'Switch on at',
            subtitle: 'Degrees',
            adjustment: new Gtk.Adjustment({lower: 150, upper: 350, step_increment: 1, page_increment: 10, value: 200}),
        });
        const hystRow = new Adw.SpinRow({
            title: 'Hysteresis',
            subtitle: 'Degrees below the switch-on angle before laptop mode returns',
            adjustment: new Gtk.Adjustment({lower: 0, upper: 45, step_increment: 1, page_increment: 5, value: 10}),
        });
        group.add(thresholdRow);
        group.add(hystRow);

        const ui = new Adw.PreferencesGroup({title: 'Top bar'});
        page.add(ui);
        const indicatorRow = new Adw.SwitchRow({title: 'Show hinge angle in the top bar'});
        ui.add(indicatorRow);
        settings.bind('show-indicator', indicatorRow, 'active', Gio.SettingsBindFlags.DEFAULT);

        let proxy = null;
        let loading = false;
        let debounce = 0;

        const readProps = () => {
            const get = n => proxy?.get_cached_property(n)?.unpack();
            const angle = get('Angle');
            if (angle === undefined) {
                statusRow.subtitle = 'service not running';
                return;
            }
            statusRow.subtitle = get('SensorPresent')
                ? `${Math.round(angle)}°, ${get('TabletMode') ? 'tablet' : 'laptop'} mode`
                : 'sensor not found';
            loading = true;
            thresholdRow.value = get('Threshold');
            hystRow.value = get('Hysteresis');
            loading = false;
        };

        const apply = () => {
            if (loading || !proxy)
                return;
            if (debounce)
                GLib.source_remove(debounce);
            debounce = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 400, () => {
                debounce = 0;
                proxy.call('SetThresholds',
                    new GLib.Variant('(uu)', [Math.round(thresholdRow.value), Math.round(hystRow.value)]),
                    Gio.DBusCallFlags.NONE, 5000, null,
                    (src, res) => {
                        try {
                            src.call_finish(res);
                        } catch (e) {
                            window.add_toast(new Adw.Toast({title: e.message}));
                            readProps();
                        }
                    });
                return GLib.SOURCE_REMOVE;
            });
        };
        thresholdRow.connect('notify::value', apply);
        hystRow.connect('notify::value', apply);

        let propsId = 0;
        const watch = Gio.bus_watch_name(
            Gio.BusType.SYSTEM, BUS_NAME, Gio.BusNameWatcherFlags.NONE,
            () => Gio.DBusProxy.new_for_bus(
                Gio.BusType.SYSTEM, Gio.DBusProxyFlags.DO_NOT_AUTO_START, null,
                BUS_NAME, OBJ_PATH, IFACE, null,
                (_s, res) => {
                    try {
                        proxy = Gio.DBusProxy.new_for_bus_finish(res);
                        propsId = proxy.connect('g-properties-changed', readProps);
                        readProps();
                    } catch (e) {
                        statusRow.subtitle = e.message;
                    }
                }),
            () => {
                proxy = null;
                statusRow.subtitle = 'service not running';
            });

        window.connect('close-request', () => {
            Gio.bus_unwatch_name(watch);
            if (debounce)
                GLib.source_remove(debounce);
            if (proxy && propsId)
                proxy.disconnect(propsId);
            window._settings = null;
        });
    }
}
