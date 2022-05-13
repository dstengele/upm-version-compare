import requests
from flask import Flask, render_template, session, request, redirect, url_for
from gevent.pywsgi import WSGIServer
from oauthlib.oauth1 import SIGNATURE_RSA
from requests import HTTPError
from requests_oauthlib import OAuth1Session
from flask_session import Session
from packaging import version
import sys
import pathlib
import random
import threading
import webbrowser

app = Flask(__name__)


def get_datadir() -> pathlib.Path:

    """
    Returns a parent directory path
    where persistent application data can be stored.

    # linux: ~/.local/share
    # macOS: ~/Library/Application Support
    # windows: C:/Users/<USER>/AppData/Roaming
    """

    home = pathlib.Path.home()

    if sys.platform == "win32":
        return home / "AppData/Roaming"
    elif sys.platform == "linux":
        return home / ".local/share"
    elif sys.platform == "darwin":
        return home / "Library/Application Support"


def get_oauth_session(instance_name):
    oauth_data = session.get("oauth", {}).get(instance_name, {})
    if (
        "access_token" not in oauth_data.keys()
        or "access_token_secret" not in oauth_data.keys()
    ):
        return {}

    instance = app.config["INSTANCES"][instance_name]

    oauth_session = OAuth1Session(
        instance["oauth"]["client_key"],
        rsa_key=instance["oauth"]["rsa_key"],
        resource_owner_key=oauth_data["access_token"],
        resource_owner_secret=oauth_data["access_token_secret"],
        signature_method=SIGNATURE_RSA,
    )

    if session.get('upm_token', None):
        oauth_session.params['token'] = session.get('upm_token')

    return oauth_session


def get_addon_data(instance_name):
    instance = app.config["INSTANCES"][instance_name]
    base_url = instance["base_url"]

    oauth_session = get_oauth_session(instance_name)

    try:
        addon_response = oauth_session.get(f"{base_url}/rest/plugins/1.0/")
        session['upm_token'] = addon_response.headers.get('upm-token', None)
        addon_data = addon_response.json()
    except Exception:
        addon_data = {}

    for addon in addon_data.get("plugins", []):
        addon["version_object"] = version.parse(addon["version"])

    return addon_data


def get_all_addon_versions(addon_key):
    versions = []
    item_index = 0
    while True:
        new_versions_reply = requests.get(
            f"https://marketplace.atlassian.com/rest/2/addons/{addon_key}/versions/",
            params={'offset': item_index, 'limit': 50}
        )
        new_versions_reply.raise_for_status()

        new_versions = new_versions_reply.json().get("_embedded", {}).get("versions", [])
        if not new_versions:
            break
        versions += new_versions
        item_index += 50
    return versions


def get_download_url(addon_key, addon_version):
    for mp_version in get_all_addon_versions(addon_key):
        if mp_version.get("name", None) == addon_version:
            return (
                mp_version.get("_embedded", {})
                .get("artifact", {})
                .get("_links", {})
                .get("binary", {})
                .get("href", None)
            )
    else:
        return None


@app.route("/install")
def install():
    addon_key = request.args.get("addon_key")
    addon_version = request.args.get("addon_version")

    install_url = get_download_url(addon_key, addon_version)

    if not install_url:
        return "Addon version not found", 500

    oauth_session = get_oauth_session("instance_b")
    base_url = app.config["INSTANCES"]["instance_b"]["base_url"]

    result = oauth_session.post(
        f"{base_url}/rest/plugins/1.0/",
        json={"pluginUri": install_url},
        headers={
            "Accept": "application/json",
            "Content-Type": "application/vnd.atl.plugins.install.uri+json",
        },
    )

    return f"""
    Response Code was: {result.status_code}.<br />
    <a href="/">Back to Overview</a>
    """, result.status_code


@app.route("/remove")
def remove():
    addon_key = request.args.get("addon_key")

    oauth_session = get_oauth_session("instance_b")
    base_url = app.config["INSTANCES"]["instance_b"]["base_url"]

    result = oauth_session.delete(f"{base_url}/rest/plugins/1.0/{addon_key}-key")

    return f"""
        Response Code was: {result.status_code}.<br />
        <a href="/">Back to Overview</a>
        """, result.status_code


@app.route("/")
def index():
    addon_data_a = {
        i["key"]: i for i in get_addon_data("instance_a").get("plugins", [])
    }
    addon_data_b = {
        i["key"]: i for i in get_addon_data("instance_b").get("plugins", [])
    }

    addon_keys_common = set(addon_data_a.keys()) & set(addon_data_b.keys())
    addon_keys_only_a = set(addon_data_a.keys()) - set(addon_data_b.keys())
    addon_keys_only_b = set(addon_data_b.keys()) - set(addon_data_a.keys())

    return render_template(
        "index.html",
        addon_data_a=addon_data_a,
        addon_data_b=addon_data_b,
        addon_keys_common=sorted(addon_keys_common),
        addon_keys_only_a=sorted(addon_keys_only_a),
        addon_keys_only_b=sorted(addon_keys_only_b),
    )


@app.route("/authorize")
def authorize():
    instance_name = request.args.get("instance")
    instance = app.config["INSTANCES"][instance_name]

    oauth_state = session.setdefault("oauth", {}).setdefault(instance_name, {})

    request_token_url = f'{instance["base_url"]}/plugins/servlet/oauth/request-token'

    oauth_session = OAuth1Session(
        instance["oauth"]["client_key"],
        rsa_key=instance["oauth"]["rsa_key"],
        callback_uri=url_for("callback", _external=True, instance=instance_name),
        signature_method=SIGNATURE_RSA,
    )

    request_token_response = oauth_session.fetch_request_token(request_token_url)

    oauth_state["request_token"] = request_token_response.get("oauth_token")
    oauth_state["request_token_secret"] = request_token_response.get(
        "oauth_token_secret"
    )

    authorization_url = f'{instance["base_url"]}/plugins/servlet/oauth/authorize'
    return redirect(oauth_session.authorization_url(authorization_url))


@app.route("/callback")
def callback():
    instance_name = request.args.get("instance")
    instance = app.config["INSTANCES"][instance_name]

    oauth_state = session.setdefault("oauth", {}).setdefault(instance_name, {})

    oauth_session = OAuth1Session(
        instance["oauth"]["client_key"],
        rsa_key=instance["oauth"]["rsa_key"],
        callback_uri=url_for("callback", _external=True, instance=instance_name),
        signature_method=SIGNATURE_RSA,
    )

    access_token_url = f'{instance["base_url"]}/plugins/servlet/oauth/access-token'

    oauth_response = oauth_session.parse_authorization_response(request.url)
    verifier = oauth_response.get("oauth_verifier")
    oauth_session = OAuth1Session(
        instance["oauth"]["client_key"],
        rsa_key=instance["oauth"]["rsa_key"],
        resource_owner_key=oauth_state["request_token"],
        resource_owner_secret=oauth_state["request_token_secret"],
        callback_uri=url_for("callback", _external=True, instance=instance_name),
        verifier=verifier,
        signature_method=SIGNATURE_RSA,
    )

    access_token_response = oauth_session.fetch_access_token(access_token_url)

    oauth_state["access_token"] = access_token_response.get("oauth_token")
    oauth_state["access_token_secret"] = access_token_response.get("oauth_token_secret")

    return redirect(url_for("index"))


def main():
    app.config.from_pyfile("../config.py")

    datadir = get_datadir() / "upm_version_compare"

    try:
        datadir.mkdir(parents=True)
    except FileExistsError:
        pass

    app.config["SESSION_TYPE"] = "filesystem"
    app.config["SESSION_FILE_DIR"] = datadir

    Session(app)

    app.config["PORT"] = 5000 + random.randint(0, 999)
    url = f"http://127.0.0.1:{app.config['PORT']}"

    threading.Timer(1.25, lambda: webbrowser.open(url)).start()

    http_server = WSGIServer(("0.0.0.0", app.config["PORT"]), app)
    try:
        http_server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
