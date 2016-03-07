from django.conf import settings
from django.core.urlresolvers import reverse
from django.http import Http404
from django.shortcuts import render
from django.views.generic import View
from django.utils.translation import ugettext_lazy

from misago.acl import add_acl
from misago.categories.models import CATEGORIES_TREE_ID, Category
from misago.categories.permissions import (
    allow_see_category, allow_browse_category)
from misago.categories.serializers import (
    BasicCategorySerializer, IndexCategorySerializer)
from misago.core.shortcuts import paginate, pagination_dict, validate_slug
from misago.readtracker import threadstracker

from misago.threads.serializers import ThreadListSerializer
from misago.threads.mixins.threadslists import ThreadsListMixin
from misago.threads.utils import add_categories_to_threads


LISTS_NAMES = {
    'my': ugettext_lazy("Your threads"),
    'new': ugettext_lazy("New threads"),
    'unread': ugettext_lazy("Unread threads"),
    'subscribed': ugettext_lazy("Subscribed threads"),
}


class BaseList(View):
    template_name = 'misago/threadslist/threads.html'
    preloaded_data_prefix = ''

    def get_subcategories(self, category, categories):
        subcategories = []
        for subcategory in categories:
            if category.has_child(subcategory):
                subcategories.append(subcategory)
        return subcategories

    def get_extra_context(self, request):
        return {}

    def set_extra_frontend_context(self, request):
        pass

    def get(self, request, **kwargs):
        try:
            page = int(request.GET.get('page', 0))
            if page == 1:
                page = None
        except ValueError:
            raise Http404()

        list_type = kwargs['list_type']

        categories = self.get_categories(request)
        category = self.get_category(request, categories, **kwargs)

        self.allow_see_list(request, category, list_type)
        subcategories = self.get_subcategories(category, categories)

        threads_categories = [category] + subcategories
        queryset = self.get_queryset(
            request, threads_categories, list_type
        ).order_by('-last_post_on')

        page = paginate(queryset, page, 24, 6)
        paginator = pagination_dict(page, include_page_range=False)

        if list_type in ('new', 'unread'):
            """we already know all threads on list are unread"""
            threadstracker.make_unread(page.object_list)
        else:
            threadstracker.make_threads_read_aware(
                request.user, page.object_list)

        add_categories_to_threads(threads_categories, page.object_list)

        visible_subcategories = []
        for thread in page.object_list:
            if (thread.top_category and
                    thread.top_category not in visible_subcategories):
                visible_subcategories.append(thread.top_category.pk)

        category.subcategories = []
        for subcategory in subcategories:
            if subcategory.pk in visible_subcategories:
                category.subcategories.append(subcategory)

        extra_context = self.get_extra_context(request)

        show_toolbar = False
        if paginator['count']:
            if category.subcategories:
                show_toolbar = True
            if request.user.is_authenticated():
                show_toolbar = True

        add_acl(request.user, page.object_list)

        request.frontend_context.update({
            'THREADS': dict(
                results=ThreadListSerializer(page.object_list, many=True).data,
                subcategories=[c.pk for c in category.subcategories],
                **paginator),
            'CATEGORIES': IndexCategorySerializer(categories, many=True).data,
        })

        if categories[0].special_role:
            request.frontend_context['CATEGORIES'][0]['special_role'] = True

        self.set_frontend_context(request)

        return render(request, self.template_name, dict(
            category=category,
            show_toolbar=show_toolbar,

            list_type=list_type,
            list_name=LISTS_NAMES.get(list_type),

            threads=page.object_list,
            paginator=paginator,
            count=paginator['count'],

            **extra_context
        ))


class ThreadsList(BaseList, ThreadsListMixin):
    template_name = 'misago/threadslist/threads.html'

    def get_categories(self, request):
        return [Category.objects.root_category()] + list(
            Category.objects.all_categories().filter(
                id__in=request.user.acl['visible_categories']
            ).select_related('parent'))

    def get_category(self, request, categories, **kwargs):
        return categories[0]

    def get_extra_context(self, request):
        return {
            'is_index': not settings.MISAGO_CATEGORIES_ON_INDEX
        }

    def set_frontend_context(self, request):
        request.frontend_context.update({
            'THREADS_API_URL': reverse('misago:api:thread-list'),
        })


class CategoryThreadsList(ThreadsList, ThreadsListMixin):
    template_name = 'misago/threadslist/category.html'
    preloaded_data_prefix = 'CATEGORY_'

    def get_category(self, request, categories, **kwargs):
        for category in categories:
            if category.pk == int(kwargs['category_id']):
                if category.special_role:
                    raise Http404()

                allow_see_category(request.user, category)
                allow_browse_category(request.user, category)

                validate_slug(category, kwargs['category_slug'])
                return category
        else:
            raise Http404()


class PrivateThreadsList(ThreadsList):
    template_name = 'misago/threadslist/private_threads.html'
    preloaded_data_prefix = 'PRIVATE_'

    def get_category(self, request, **kwargs):
        return Category.objects.private_threads()

    def get_subcategories(self, request, category):
        return []