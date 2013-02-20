from django import forms
from django.conf import settings
from django.core.urlresolvers import reverse
from django.http import (HttpResponse, HttpResponseBadRequest,
    HttpResponseGone, HttpResponseForbidden, HttpResponseNotFound,
    HttpResponsePermanentRedirect, HttpResponseRedirect)
from django.shortcuts import render_to_response
from django.template import RequestContext
from django.utils import simplejson
from django.utils.translation import ugettext as _

import pymongo


__all__ = [
    'add',
    'follow',
]

BASE85 = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz' \
    '!#$%&()*+-;<=>?@^_`{|}~'
BASE62 = BASE85[:62]

DURATION_CHOICES = (
    ("PT10M", " ".join(["10", _("Minutes")])),
    ("PT30M", " ".join(["30", _("Minutes")])),
    ("PT1H", " ".join(["1", _("Hour")])),
    ("P1D", " ".join(["1", _("Day")])),
    ("P1W", " ".join(["1", _("Week")])),
    ("P1M", " ".join(["1", _("Month")])),
    # ("", _("Never")),
)

# See RFC2616 for details.
# http://tools.ietf.org/html/rfc2616#section-10.2
HTTP_STATUS_OK = 200
HTTP_STATUS_CREATED = 201
HTTP_STATUS_ACCEPTED = 202
HTTP_STATUS_NO_CONTENT = 204
HTTP_STATUS_RESET_CONTENT = 205
HTTP_STATUS_PARTIAL_CONTENT = 206
HTTP_STATUS_PAYMENT_REQUIRED = 402
HTTP_STATUS_METHOD_NOT_ALLOWED = 405
HTTP_STATUS_NOT_ACCEPTABLE = 406
HTTP_STATUS_CONFLICT = 409
HTTP_STATUS_PRECONDITION_FAILED = 412
HTTP_STATUS_REQUEST_ENTITY_TOO_LARGE = 413
HTTP_STATUS_UNPROCESSABLE_ENTITY = 422


class JSONEncoder(simplejson.JSONEncoder):

    def default(self, obj):
        """Tests the input object, obj, to encode as JSON."""
        if hasattr(obj, '__json__'):
            return getattr(obj, '__json__')()

        import datetime
        import isodate
        if isinstance(obj, datetime.datetime):
            return isodate.datetime_isoformat(obj)
        elif isinstance(obj, datetime.date):
            return isodate.date_isoformat(obj)
        elif isinstance(obj, datetime.time):
            return isodate.time_isoformat(obj)
        elif isinstance(obj, datetime.timedelta):
            return isodate.duration_isoformat(obj)

        return simplejson.JSONEncoder.default(self, obj)


class LinkForm(forms.Form):
    target_url = forms.URLField(label=_('Target URL'))
    is_public = forms.BooleanField(required=False, initial=True,
        label=_('Public (can be used without being logged on)'))
    duration = forms.ChoiceField(required=False, choices=DURATION_CHOICES,
        initial=DURATION_CHOICES[2][0], label=_('Expires'))


def make_dto(**data):
    """ Makes a data transfer object.

        dto = make_dto(title="Lorem Ipsum")
        print dto.title
    """

    class final(type):
        """ Metaclass to prevent a class from being inherited """

        def __init__(cls, name, bases, namespace):
            super(final, cls).__init__(name, bases, namespace)
            for klass in bases:
                if isinstance(klass, final):
                    raise TypeError(str(klass.__name__) + " is final")

    class DataTransferObject(object):
        __metaclass__ = final

        def __init__(self, **kwargs):
            self.__dict__ = kwargs

        def __iter__(self):
            return self.__dict__

        def __repr__(self):
            return self.__dict__.__repr__()

        def as_dict(self):
            return self.__dict__

        def get(self, name, default=None):
            return self.__dict__.get(name, default)

    return DataTransferObject(**data)


def is_link_id_unique(id):
    return get_link(id) is None


def get_random_link_id(is_unique=None):
    import random
    return ''.join(random.sample(BASE62, 5))


def link_is_expired(link):
    expiration_date = link.get("expiration_date")
    if not expiration_date:
        return False
    import datetime
    return expiration_date <= datetime.datetime.now()


def get_link(val):
    """ Get link by its id. """
    if isinstance(val, basestring):
        link = None
        try:
            conn = pymongo.Connection()
            db = conn[settings.APPLICATION_NAME]
            link = db.links.find_one({'uid': val})
        except Exception:
            pass
        finally:
            conn.disconnect()
        return link


def make_link_data(**data):
    import datetime
    now = datetime.datetime.now()
    defaults = {
        'uid': get_random_link_id(is_link_id_unique),
        'owner': None,
        'date_created': now,
        'expiration_date': now + datetime.timedelta(hours=1),
        'url': None,
        'target_url': None,
        'is_public': True,
        'visits': {},
        'last_visited': None,
    }
    fields = {}
    for field, default in defaults.iteritems():
        fields[field] = data.get(field, default)
    return make_dto(**fields)


def update_link(val, data=None, keys=None):
    if data is None:
        data = {}
    if keys is None:
        keys = []
    if isinstance(keys, basestring):
        keys = keys.split(",")
    keys += data.keys()

    # data = _to_dict(val, data)
    # remove items not listed in keys
    if keys and hasattr(keys, '__contains__'):
        for key in data.keys():
            if not key in keys:
                del data[key]
    return data


def save_link(val):
    try:
        conn = pymongo.Connection()
        db = conn[settings.APPLICATION_NAME]
        # ensure database exists
        db.collection_names()
        db.links.insert(val.as_dict())
    except (Exception):
        val = None
    finally:
        conn.disconnect()
    return val


def save_link_from_request(request, data={}):
    _data = data.copy()
    duration = _data.pop("duration")
    link = make_link_data(**_data)

    if duration:
        try:
            import isodate
            link.expiration_date = link.date_created + \
                    isodate.parse_duration(duration)
        except Exception:
            pass
    link.owner = [None, request.user.email][request.user.is_authenticated()]
    link.url = request.build_absolute_uri(reverse('tmp_link_follow',
        args=[link.uid]))
    return save_link(link)


def update_link_from_request(data, request):
    """ Update link transfer object with HTTP request data.
    """
    if data:
        data.owner = [None, request.user.email][request.user.is_authenticated()]
        if data.uid:
            data.url = request.build_absolute_uri(reverse('tmp_link_follow',
                args=[data.uid]))
    return data


def _get_json_indent(request):
    """ `prettyPrint` If set to "true", data output will include line breaks
    and indentation to make it more readable. If set to "false", unnecessary
    whitespace is removed, reducing the size of the response. Defaults to
    "true".
    """
    _PARAM = "pretty"
    if (not _PARAM in request.GET or request.GET[_PARAM] == "true"):
        return 2


def api_shorten(request):
    if not request.is_ajax() or request.method != 'POST':
        return HttpResponse(status=HTTP_STATUS_METHOD_NOT_ALLOWED)

    form = LinkForm(data=request.POST)
    response = {}
    if form.is_valid():
        link = make_link_data(**form.cleaned_data)
        update_link_from_request(link, request)
        link = save_link(link)
        if link:
            if '_id' in link.as_dict():
                del(link.as_dict()['_id'])
            del(link.as_dict()['owner'])
            response = {
                "link": link.as_dict(),
            }
            status = HTTP_STATUS_CREATED
    else:
        status = HTTP_STATUS_UNPROCESSABLE_ENTITY
        response = {
            "errors": [(k, v) for k, v in form.errors.items()]
        }

    from utils.django import render_to_json_response
    return render_to_json_response(response, status=status,
        encoder=JSONEncoder, indent=_get_json_indent(request))


def add(request, template_name="tmp/link_form.html", form_class=LinkForm,
    success_url=None, extra_context=None, id_format=None):
    auto_id = id_format or "id_%s"
    context = extra_context or {}

    if not success_url:
        from django.core.urlresolvers import reverse
        success_url = reverse('tmp_link_add')

    instance = {
        "is_public": True,
        "duration": DURATION_CHOICES[2][0],
    }
    if request.method != 'POST':
        form = form_class(instance, auto_id=auto_id)
        context.update(form=form)
        return render_to_response(template_name, context,
            context_instance=RequestContext(request))

    # proceed posted data
    form = form_class(data=request.POST, files=request.FILES, auto_id=auto_id)
    context.update(form=form)
    if form.is_valid():
        link = save_link_from_request(request, form.cleaned_data)
        from django.contrib import messages
        messages.success(request,
            _(u"Link \"%s\" successfully created.") % link.url)
        if success_url:
            next_url = success_url
            if callable(success_url):
                next_url = success_url(link)
            return HttpResponseRedirect(next_url)

    return render_to_response(template_name, context,
        context_instance=RequestContext(request))


def follow(request, uid):
    """Toggle post published state."""
    link = get_link(uid)
    if not link:
        return HttpResponseNotFound()

    # is_public = link.get('is_public', True)
    # if not is_public and link.get('owner') != request.user.email:
    #     return HttpResponseForbidden()
    # if not link.get("is_public"):
    #     return HttpResponseForbidden()

    if link_is_expired(link):
        return HttpResponseGone()

    return HttpResponsePermanentRedirect(link.get("target_url"))
