# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Riel
"""Updates through Blender's own extension repositories. Enable Updates (the add-on's preferences) adds the HairMesh
repository (INDEX_URL) to Preferences > Get Extensions, at most once (found again by its URL: `repos.new` takes a
repeated name as "HairMesh.001"), and has Blender sync it; from then on Blender checks it at start-up and its Get
Extensions lists the new versions. The notice on the preferences page only reads what Blender already synced, the
repository's `.blender_ext/index.json` (a file format Blender keeps for its repositories, version "v1"), never the
network: this add-on downloads nothing itself.

Nothing runs without the click: register() adds no repository and syncs nothing. Without online access
(bpy.app.online_access) the button is dimmed and its click only says where to turn it on.

A copy installed from disk (the zip, in the User Default repository) is not updated by Blender from another
repository; the notice says so (measured on Blender 5.1.2: `extensions.package_upgrade_all` leaves it as it is)."""
import json
import os
import tomllib

import bpy
from bpy.props import StringProperty

INDEX_URL = 'https://riel-gab.github.io/hairmesh/extensions/index.json'
REPO_NAME = 'HairMesh'
REPO_MODULE = 'hairmesh'
PKG_ID = 'hairmesh'                                                 # the id of blender_manifest.toml
MANIFEST = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'blender_manifest.toml')
_PACKAGE = __name__.rsplit('.', 2)[0]                              # 'hairmesh', or 'bl_ext.<repository>.hairmesh' installed


# ---------------- versions (pure) ----------------

def version_key(s):
    """a comparable key of a semantic version ("1.2.0", "1.1.0-dev", "1.0.0+build"), or None when it is not one: a
    release is above its pre-releases, numeric identifiers compare as numbers and below the alphanumeric ones"""
    if not isinstance(s, str):
        return None
    core, dash, pre = s.strip().split('+', 1)[0].partition('-')
    parts = core.split('.')
    if len(parts) != 3 or not all(p.isdigit() for p in parts) or (dash and not pre):
        return None
    if not dash:
        return (*map(int, parts), (1,))
    ids = []
    for i in pre.split('.'):
        if not i:
            return None
        ids.append((0, int(i), '') if i.isdigit() else (1, 0, i))
    return (*map(int, parts), (0, *ids))


def newer(remote, local):
    """True when the version `remote` is above `local` (both semantic versions; anything else: False)"""
    a, b = version_key(remote), version_key(local)
    return a is not None and b is not None and a > b


def normalize_url(url):
    """the URL as two repositories are compared: no spaces around, no slash at the end, the scheme and host in lower case"""
    u = (url or '').strip().rstrip('/')
    scheme, sep, rest = u.partition('://')
    if not sep:
        return u
    host, slash, path = rest.partition('/')
    return f'{scheme.lower()}://{host.lower()}{slash}{path}'


def best_version(index, pkg_id=PKG_ID):
    """the highest version of `pkg_id` in a repository index ({"data": [{"id", "version"}, ...]}), or None"""
    best = None
    data = index.get('data') if isinstance(index, dict) else None
    for item in data if isinstance(data, list) else ():
        if isinstance(item, dict) and item.get('id') == pkg_id and version_key(item.get('version')) is not None:
            if best is None or newer(item['version'], best):
                best = item['version']
    return best


# ---------------- what Blender holds ----------------

_installed = []


def installed_version():
    """the version of this add-on, from its own blender_manifest.toml (None when it cannot be read)"""
    if not _installed:
        try:
            with open(MANIFEST, 'rb') as f:
                _installed.append(tomllib.load(f).get('version'))
        except (OSError, ValueError):
            _installed.append(None)
    return _installed[0]


def installed_repo_module(package=None):
    """the module of the repository this add-on is installed in ('user_default' for a zip installed from disk), or
    None when it is not installed as an extension"""
    parts = (package or _PACKAGE).split('.')
    return parts[1] if len(parts) == 3 and parts[0] == 'bl_ext' else None


def online():
    """Blender may use the network (Preferences > System > Allow Online Access, or --online-mode)"""
    return bool(bpy.app.online_access)


def find_repo(context=None, url=None):
    """the repository of `url` (INDEX_URL by default) in the preferences, or None"""
    want = normalize_url(url or INDEX_URL)
    repos = (context or bpy.context).preferences.extensions.repos
    return next((r for r in repos if r.use_remote_url and normalize_url(r.remote_url) == want), None)


def is_on(context=None, url=None):
    """the repository is in the preferences and enabled"""
    r = find_repo(context, url)
    return r is not None and r.enabled


def index_path(repo):
    """where Blender keeps the index it synced of a remote repository"""
    return os.path.join(repo.directory, '.blender_ext', 'index.json')


_index_cache = {}


def read_index(path):
    """the index at `path` (None when missing or not JSON), read again only when the file changes (the page draws often)"""
    try:
        st = os.stat(path)
    except OSError:
        _index_cache.pop(path, None)
        return None
    stamp = (st.st_mtime_ns, st.st_size)
    hit = _index_cache.get(path)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = None
    _index_cache[path] = (stamp, data)
    return data


def available(context=None, url=None):
    """(version, in_place) when the synced index of the HairMesh repository has a version above the installed one,
    else None; in_place: this copy was installed from that repository, so Blender can update it"""
    r = find_repo(context, url)
    if r is None or not r.enabled:
        return None
    index = read_index(index_path(r))
    if index is None:
        return None
    v = best_version(index)
    if v is None or not newer(v, installed_version()):
        return None
    return v, installed_repo_module() == r.module


def sync(context, repo):
    """has Blender sync one repository: in the background (its own modal job) with a window, blocking without one"""
    if bpy.app.background or context.window is None:
        return bpy.ops.extensions.repo_sync(repo_directory=repo.directory)
    return bpy.ops.extensions.repo_sync('INVOKE_DEFAULT', repo_directory=repo.directory)


def offline_reason():
    if bpy.app.online_access_override:
        return 'Blender was started with --offline-mode: start it without that option to check for updates'
    return 'Online access is off: turn on Allow Online Access in Preferences > System, then click Enable Updates again'


# ---------------- the button ----------------

class HAIRMESH_OT_enable_updates(bpy.types.Operator):
    """Add the HairMesh repository to Get Extensions, so Blender checks it for new versions"""
    bl_idname = 'hairmesh.enable_updates'
    bl_label = 'Enable Updates'
    bl_options = {'REGISTER', 'INTERNAL'}

    url: StringProperty(name='URL', description='Set the URL of the repository index (empty: the HairMesh repository)', default='', options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, context, properties):
        if not online():
            return 'Turn on Allow Online Access in Preferences > System first: the updates come from the network'
        return ''                                                   # (Blender's way to say: the docstring)

    def execute(self, context):
        if not online():
            self.report({'WARNING'}, offline_reason())
            return {'CANCELLED'}
        url = self.url or INDEX_URL
        repo = find_repo(context, url)
        if repo is None:
            repo = context.preferences.extensions.repos.new(name=REPO_NAME, module=REPO_MODULE, remote_url=url)
            made = True
        else:
            made = False
        repo.enabled = True
        repo.use_sync_on_startup = True                             # (Blender checks it at every start-up from now on)
        try:
            sync(context, repo)
        except RuntimeError as e:                                   # (a failed sync leaves the repository in place)
            self.report({'WARNING'}, f'HairMesh repository added; checking it failed: {e}')
            return {'FINISHED'}
        self.report({'INFO'}, 'HairMesh repository added to Get Extensions' if made else 'HairMesh repository already in Get Extensions: checking it again')
        return {'FINISHED'}


def draw(layout, context, title_row):
    """the Updates box of the preferences page: Enable Updates, or the state once the repository is in; a line when the
    synced repository has a newer version"""
    box = layout.box()
    right = title_row(box, 'Updates', 'URL')
    if is_on(context):
        right.label(text='Repository Added', icon='CHECKMARK')
    else:
        right.active = online()                                     # (dimmed offline; the click says where to turn it on)
        right.operator(HAIRMESH_OT_enable_updates.bl_idname, text='Enable Updates', icon='IMPORT')
    news = available(context)
    if news is None:
        return
    version, in_place = news
    row = box.row()
    row.label(text=f'HairMesh {version} Is Available', icon='INFO')
    right = row.row()
    right.alignment = 'RIGHT'
    op = right.operator('wm.context_set_enum', text='Get Extensions', icon='PREFERENCES')
    op.data_path = 'preferences.active_section'
    op.value = 'EXTENSIONS'
    if not in_place:
        box.label(text='Installed From Disk: Uninstall It, Then Install It From the HairMesh Repository', icon='BLANK1')


_CLASSES = (HAIRMESH_OT_enable_updates,)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
