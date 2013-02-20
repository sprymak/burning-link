from django.conf.urls.defaults import patterns, url


urlpatterns = patterns('tmp_www.views',
    url(r'^$', 'add', name='tmp_link_add'),
    url(r'^api/shorten$', 'api_shorten', name='tmp_link_api_shorten'),
    url(r'^(?P<uid>[\w-]+)$', 'follow', name='tmp_link_follow'),
)
