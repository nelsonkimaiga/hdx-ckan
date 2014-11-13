import ckan.controllers.storage as storage
import os
import re
import urllib
import uuid
from datetime import datetime
from cgi import FieldStorage

from ofs import get_impl
from pylons import request, response
from pylons.controllers.util import abort, redirect_to
from pylons import config
from paste.fileapp import FileApp
from paste.deploy.converters import asbool

from ckan.lib.base import BaseController, c, request, render, config, h, abort
from ckan.lib.jsonp import jsonpify
import ckan.model as model
import ckan.logic as logic

try:
    from cStringIO import StringIO
except ImportError:
    from StringIO import StringIO
try:
    import json
except:
    import simplejson as json

from logging import getLogger
log = getLogger(__name__)


BUCKET = config.get('ckan.storage.bucket', 'default')
key_prefix = config.get('ckan.storage.key_prefix', 'file/')

_eq_re = re.compile(r"^(.*)(=[0-9]*)$")


def generate_response(http_status, unicode_body, no_cache=True):
    r = request.environ['pylons.pylons'].response
    if no_cache:
        r.headers['Pragma'] = 'no-cache'
        r.headers['Cache-Control'] = 'no-cache'

    r.unicode_body = unicode_body
    r.status = http_status
    return r


class FileDownloadController(storage.StorageController):
    _ofs_impl = None

    @property
    def ofs(self):
        if not FileDownloadController._ofs_impl:
            FileDownloadController._ofs_impl = get_ofs()
        return FileDownloadController._ofs_impl

    def file(self, label):
        from sqlalchemy.engine import create_engine
        #from label find resource id
        url = config.get('ckan.site_url', '') +'/storage/f/'+ urllib.quote(label)
        engine = create_engine(config.get('sqlalchemy.url', ''), echo=True)
        connection = engine.connect()
        query = connection.execute("""SELECT * from resource where url= %s""", (url,))
        res = query.fetchone()
        if not res:
#             raise logic.NotFound
            r = generate_response(404, u'File not found')
            return r

        # We need this as a resource object to check access so create a dummy obj and trick CKAN
        resource = model.Resource()
        for k in res.keys():
            setattr(resource,k,res[k])

        context = {'model': model, 'session': model.Session,
                   'user': c.user or c.author, 'for_view': True,
                   'auth_user_obj': c.userobj, 'resource':resource}
        data_dict = {'id': resource.id}
        try:
            logic.check_access('resource_show', context, data_dict)
        except logic.NotAuthorized:
            r = generate_response(403, u'Not authorized to read file ' + resource.id)
            return r

        exists = self.ofs.exists(BUCKET, label)
        if not exists:
            # handle erroneous trailing slash by redirecting to url w/o slash
            if label.endswith('/'):
                label = label[:-1]
                # This may be best being cached_url until we have moved it into
                # permanent storage
                file_url = h.url_for('storage_file', label=label)
                h.redirect_to(file_url)
            else:
#                 abort(404)
                r = generate_response(404, u'File not found')
                return r

        file_url = self.ofs.get_url(BUCKET, label)
        if file_url.startswith("file://") or file_url.endswith('xlsx'):
            metadata = self.ofs.get_metadata(BUCKET, label)
            filepath = file_url[len("file://"):]
            headers = {
                # 'Content-Disposition':'attachment; filename="%s"' % label,
                'Pragma': 'no-cache',
                'Cache-Control': 'no-cache',
                'Content-Type': metadata.get('_format', 'text/plain')}
            fapp = FileApp(filepath, headers=None, **headers)
            return fapp(request.environ, self.start_response)
        else:
            h.redirect_to(file_url.encode('ascii', 'ignore'))



def create_pairtree_marker(folder):
    """ Creates the pairtree marker for tests if it doesn't exist """
    if not folder[:-1] == '/':
        folder = folder + '/'

    directory = os.path.dirname(folder)
    if not os.path.exists(directory):
        os.makedirs(directory)

    target = os.path.join(directory, 'pairtree_version0_1')
    if os.path.exists(target):
        return

    open(target, 'wb').close()


def get_ofs():
        """Return a configured instance of the appropriate OFS driver.
        """
        storage_backend = config['ofs.impl']
        kw = {}
        for k, v in config.items():
            if not k.startswith('ofs.') or k == 'ofs.impl':
                continue
            kw[k[4:]] = v

        # Make sure we have created the marker file to avoid pairtree issues
        if storage_backend == 'pairtree' and 'storage_dir' in kw:
            create_pairtree_marker(kw['storage_dir'])

        ofs = get_impl(storage_backend)(**kw)
        return ofs