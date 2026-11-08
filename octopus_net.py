"""Reconcile the Pro management API without flashing or rebooting the appliance."""

import argparse
import datetime
from html.parser import HTMLParser
import http.cookiejar
import json
import os
from pathlib import Path
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


class ConfigurationError(Exception):
    """An operational error safe to include in logs."""


class Forms(HTMLParser):
    """Read successful form controls, matching jQuery.serializeArray()."""

    def __init__(self, html):
        super().__init__()
        self.forms = []
        self.form = None
        self.select = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.form = {"classes": attrs.get("class", "").split(), "values": {}}
        if self.form is None or "disabled" in attrs:
            return
        if tag == "input" and attrs.get("name"):
            kind = attrs.get("type", "text")
            if kind in ("submit", "button", "file"):
                return
            if kind in ("checkbox", "radio") and "checked" not in attrs:
                return
            self.form["values"][attrs["name"]] = attrs.get(
                "value", "on" if kind == "checkbox" else ""
            )
        elif tag == "select" and attrs.get("name"):
            self.select = {"name": attrs["name"], "first": None, "selected": None}
        elif tag == "option" and self.select is not None:
            value = attrs.get("value", "")
            if self.select["first"] is None:
                self.select["first"] = value
            if "selected" in attrs:
                self.select["selected"] = value

    def handle_endtag(self, tag):
        if tag == "select" and self.select is not None:
            select = self.select
            self.form["values"][select["name"]] = (
                select["selected"]
                if select["selected"] is not None
                else select["first"]
            )
            self.select = None
        elif tag == "form" and self.form is not None:
            self.forms.append(self.form)
            self.form = None


def tuner_settings(html):
    forms = Forms(html).forms

    def values(name):
        return [form["values"] for form in forms if name in form["classes"]]

    tuners = values("settings")
    if not tuners or any("adapter" not in t or "frontend" not in t for t in tuners):
        raise ConfigurationError("Unrecognized tuner API response; refusing to write")
    return {
        "max": next(iter(values("max-settings")), {}),
        "delsys-mask": next(iter(values("satip-delsys-mask")), {}),
        "unicable-database": next(iter(values("unicable-database")), {}),
        "tuners": tuners,
    }


def plan_tuners(current, desired):
    result = json.loads(json.dumps(current))
    actual = {(t["adapter"], t["frontend"]): t for t in result["tuners"]}
    expected = [(t["adapter"], t["frontend"]) for t in desired]
    if (
        len(actual) != len(result["tuners"])
        or len(set(expected)) != len(expected)
        or set(actual) != set(expected)
    ):
        raise ConfigurationError(
            "Tuner identity/count differs from the declared configuration"
        )
    changes = []
    for tuner in desired:
        key = tuner["adapter"], tuner["frontend"]
        for field, value in tuner.items():
            before = actual[key].get(field, "false" if field == "enable" else None)
            if before != value:
                changes.append(f"{key[0]}:{key[1]} {field}")
                if field == "enable" and value == "false":
                    actual[key].pop(field, None)
                else:
                    actual[key][field] = value
    return result, changes


def verify_identity(xml, config):
    root = ET.fromstring(xml)
    info = {node.tag.split("}")[-1]: (node.text or "").strip() for node in root.iter()}
    for field, expected in (
        ("UDN", "uuid:" + config["expectedUuid"]),
        ("serialNumber", config["expectedSerial"]),
        ("X_SATIPCAP", config["expectedCapability"]),
    ):
        if info.get(field) != expected:
            raise ConfigurationError(f"Appliance {field} does not match; refusing to write")
    return info


def reconcile(mode, profile, password_file=None, state_directory=None):
    config = json.loads(Path(profile).read_text())
    base = "http://" + config["host"]
    session = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )

    def call(path, data=None, form=False):
        headers = {}
        if data is not None:
            headers["Content-Type"] = (
                "application/x-www-form-urlencoded" if form else "application/json"
            )
            data = (urllib.parse.urlencode(data) if form else json.dumps(data)).encode()
        with session.open(
            urllib.request.Request(base + path, data=data, headers=headers), timeout=15
        ) as response:
            return response.read().decode()

    verify_identity(call("/octoserve/octonet.xml"), config)
    password_path = (
        Path(password_file)
        if password_file is not None
        else Path(os.environ["CREDENTIALS_DIRECTORY"]) / "password"
    )
    password = password_path.read_text().strip()
    if not password:
        raise ConfigurationError("The runtime password is empty")
    home = call("/")
    if 'action="/initialpassword"' in home:
        if mode != "apply":
            raise ConfigurationError("Initial admin password has not been provisioned")
        call("/initialpassword", {"passwd": password, "submit": "Save"}, form=True)
    call(
        "/login",
        {
            "login": config["username"],
            "passwd": password,
            "next": "/",
            "lang": "en",
            "submit": "Login",
        },
        form=True,
    )
    system = call("/systemsettings")
    match = re.search(r"octonet-pro-\d+\.\d+\.\d+", system)
    if match is None or match[0] != config["firmwareVersion"]:
        raise ConfigurationError(
            "Firmware differs from the pinned version, or login failed; refusing to write"
        )
    current = tuner_settings(call("/tuner"))
    target, changes = plan_tuners(current, config["tuners"])
    print("Verified appliance identity and firmware")
    if changes:
        print("\n".join(changes))
        if mode == "apply":
            state = Path(state_directory or os.environ["STATE_DIRECTORY"])
            state.mkdir(mode=0o700, parents=True, exist_ok=True)
            if state.is_symlink() or state.stat().st_mode & 0o077:
                raise ConfigurationError("State directory must be private with mode 0700")
            stamp = datetime.datetime.now(datetime.timezone.utc).strftime(
                "%Y%m%dT%H%M%S%fZ"
            )
            backup = state / f"tuners-before-{stamp}.json"
            with open(backup, "x", opener=lambda path, flags: os.open(path, flags, 0o600)) as handle:
                handle.write(json.dumps(current, indent=2) + "\n")
            if json.loads(call("/tuner/save", target)) is not True:
                raise ConfigurationError("Appliance rejected tuner settings")
            _, remaining = plan_tuners(tuner_settings(call("/tuner")), config["tuners"])
            if remaining:
                raise ConfigurationError("Tuner settings did not persist")
            print("Tuner configuration applied and verified")
        else:
            return 2
    else:
        print("Tuner configuration matches; no writes or reboot needed")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["check", "apply"])
    parser.add_argument("profile", help="Path to a public JSON device profile")
    parser.add_argument("--password-file", help="Runtime password path; defaults to the systemd credential")
    parser.add_argument("--state-dir", help="Private backup directory; defaults to STATE_DIRECTORY")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        return reconcile(args.mode, args.profile, args.password_file, args.state_dir)
    except ConfigurationError as error:
        print(f"octopus-net-nix: {error}", file=sys.stderr)
    except Exception as error:
        print(
            f"octopus-net-nix: operation failed ({type(error).__name__}); details suppressed to protect credentials",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
