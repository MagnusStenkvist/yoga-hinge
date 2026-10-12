import GObject from 'gi://GObject';
import Gio from 'gi://Gio';
import St from 'gi://St';
import Clutter from 'gi://Clutter';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

const BUS_NAME = 'org.yogahinge';
const OBJ_PATH = '/org/yogahinge/Hinge';
const IFACE = 'org.yogahinge.Hinge1';

const Indicator = GObject.registerClass(
class YogaHingeIndicator extends PanelMenu.Button {
    _init(extension) {
        super._init(0.0, 'Yoga Hinge');
        this._extension = extension;

        const box = new St.BoxLayout({style_class: 'panel-status-menu-box'});
        this._icon = new St.Icon({
            icon_name: 'computer-symbolic',
            style_class: 'system-status-icon',
        });
        this._label = new St.Label({
            text: '–',
            y_align: Clutter.ActorAlign.CENTER,
        });
        box.add_child(this._icon);
        box.add_child(this._label);
        this.add_child(box);

        this._angleItem = new PopupMenu.PopupMenuItem('Hinge angle: –', {reactive: false});
        this._modeItem = new PopupMenu.PopupMenuItem('Mode: –', {reactive: false});
        this._limitItem = new PopupMenu.PopupMenuItem('Tablet mode from: –', {reactive: false});
        this.menu.addMenuItem(this._angleItem);
        this.menu.addMenuItem(this._modeItem);
        this.menu.addMenuItem(this._limitItem);
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this.menu.addAction('Settings…', () => extension.openPreferences());
    }

    update(p) {
        if (!p) {
            this._label.text = '–';
            this._angleItem.label.text = 'yoga-hinged service not running';
            this._modeItem.label.text = 'Mode: –';
            this._limitItem.label.text = 'Tablet mode from: –';
            return;
        }
        const angle = Math.round(p.angle);
        this._label.text = p.present ? `${angle}°` : '?';
        this._angleItem.label.text = p.present
            ? `Hinge angle: ${angle}°`
            : 'Sensor not found';
        this._modeItem.label.text = `Mode: ${p.tablet ? 'tablet' : 'laptop'}`;
        this._limitItem.label.text =
            `Tablet mode from: ${p.threshold}° (back below ${p.threshold - p.hysteresis}°)`;
        this._icon.icon_name = p.tablet ? 'input-tablet-symbolic' : 'computer-symbolic';
    }
});

export default class YogaHingeExtension extends Extension {
    enable() {
        this._settings = this.getSettings();
        this._indicator = null;
        this._proxy = null;
        this._nameWatch = Gio.bus_watch_name(
            Gio.BusType.SYSTEM, BUS_NAME, Gio.BusNameWatcherFlags.NONE,
            () => this._connect(), () => this._disconnect());
        this._settingsId = this._settings.connect(
            'changed::show-indicator', () => this._syncIndicator());
        this._syncIndicator();
    }

    disable() {
        if (this._nameWatch)
            Gio.bus_unwatch_name(this._nameWatch);
        this._nameWatch = null;
        if (this._settingsId)
            this._settings.disconnect(this._settingsId);
        this._settingsId = null;
        this._disconnect();
        this._removeIndicator();
        this._settings = null;
    }

    _connect() {
        Gio.DBusProxy.new_for_bus(
            Gio.BusType.SYSTEM, Gio.DBusProxyFlags.DO_NOT_AUTO_START, null,
            BUS_NAME, OBJ_PATH, IFACE, null,
            (_src, res) => {
                try {
                    const proxy = Gio.DBusProxy.new_for_bus_finish(res);
                    if (!this._nameWatch) // disabled meanwhile
                        return;
                    this._proxy = proxy;
                    this._proxyId = proxy.connect(
                        'g-properties-changed', () => this._refresh());
                    this._refresh();
                } catch (e) {
                    console.error(`yoga-hinge: ${e.message}`);
                }
            });
    }

    _disconnect() {
        if (this._proxy && this._proxyId)
            this._proxy.disconnect(this._proxyId);
        this._proxyId = null;
        this._proxy = null;
        this._indicator?.update(null);
    }

    _read() {
        const get = n => this._proxy?.get_cached_property(n)?.unpack();
        const angle = get('Angle');
        if (angle === undefined)
            return null;
        return {
            angle,
            tablet: get('TabletMode'),
            threshold: get('Threshold'),
            hysteresis: get('Hysteresis'),
            present: get('SensorPresent'),
        };
    }

    _refresh() {
        this._indicator?.update(this._read());
    }

    _syncIndicator() {
        if (this._settings.get_boolean('show-indicator')) {
            if (!this._indicator) {
                this._indicator = new Indicator(this);
                Main.panel.addToStatusArea(this.uuid, this._indicator);
                this._refresh();
            }
        } else {
            this._removeIndicator();
        }
    }

    _removeIndicator() {
        this._indicator?.destroy();
        this._indicator = null;
    }
}
