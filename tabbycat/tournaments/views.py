import logging
from threading import Lock

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model, login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.urlresolvers import reverse, reverse_lazy
from django.http import Http404, HttpResponse
from django.shortcuts import redirect
from django.views.generic.base import RedirectView, TemplateView
from django.views.generic.edit import CreateView, FormView

from draw.models import Debate
from utils.forms import SuperuserCreationForm
from utils.misc import redirect_round, redirect_tournament
from utils.mixins import CacheMixin, PostOnlyRedirectView, SuperuserRequiredMixin

from .forms import TournamentForm
from .mixins import RoundMixin, TournamentMixin
from .models import Tournament

User = get_user_model()
logger = logging.getLogger(__name__)


class PublicSiteIndexView(CacheMixin, TemplateView):
    template_name = 'site_index.html'
    cache_timeout = 10 # Set slower to show new indexes so it will show new pages

    def get(self, request, *args, **kwargs):
        tournaments = Tournament.objects.all()
        if tournaments.count() == 1 and not request.user.is_authenticated():
            logger.debug('One tournament only, user is: %s, redirecting to tournament-public-index', request.user)
            return redirect_tournament('tournament-public-index', tournaments.first())
        elif not tournaments.exists() and not User.objects.exists():
            logger.debug('No users and no tournaments, redirecting to blank-site-start')
            return redirect('blank-site-start')
        else:
            return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        kwargs['tournaments'] = Tournament.objects.all()
        return super().get_context_data(**kwargs)


class TournamentPublicHomeView(CacheMixin, TournamentMixin, TemplateView):
    template_name = 'public_tournament_index.html'
    cache_timeout = 10 # Set slower to show new indexes so it will show new pages


class TournamentAdminHomeView(LoginRequiredMixin, TournamentMixin, TemplateView):
    template_name = "tournament_index.html"

    def get_context_data(self, **kwargs):
        tournament = self.get_tournament()
        round = tournament.current_round
        assert(round is not None)
        kwargs["round"] = round
        kwargs["readthedocs_version"] = settings.READTHEDOCS_VERSION
        kwargs["blank"] = not (tournament.team_set.exists() or tournament.adjudicator_set.exists() or tournament.venue_set.exists())
        return super().get_context_data(**kwargs)

    def get(self, request, *args, **kwargs):
        tournament = self.get_tournament()
        if tournament.current_round is None:
            if self.request.user.is_superuser:
                tournament.current_round = tournament.round_set.order_by('seq').first()
                if tournament.current_round is None:
                    return HttpResponse('<p>Error: This tournament has no rounds; '
                                        ' you\'ll need to add some in the '
                                        '<a href="' + reverse('admin:tournaments_round_changelist') +
                                        '">Edit Database</a> area.</p>')
                messages.warning(self.request, "The current round wasn't set, "
                                 "so it's been automatically set to the first round.")
                logger.warning("Automatically set current round to {}".format(tournament.current_round))
                tournament.save()
                self.request.tournament = tournament  # Update for context processors
            else:
                raise Http404()
        return super().get(self, request, *args, **kwargs)


class RoundIncrementConfirmView(SuperuserRequiredMixin, RoundMixin, TemplateView):
    template_name = 'round_increment_check.html'

    def get(self, request, *args, **kwargs):
        round = self.get_round()
        current_round = self.get_tournament().current_round
        if round != current_round:
            messages.warning(self.request, 'You are trying to advance to ' +
                round.name + ' but the current round is ' + current_round.name +
                ' — advance to ' + round.prev.name + ' first!')
            return redirect_round('results', self.get_tournament().current_round)
        else:
            return super().get(self, request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        kwargs['num_unconfirmed'] = self.get_round().get_draw().filter(
            result_status__in=[Debate.STATUS_NONE, Debate.STATUS_DRAFT]).count()
        kwargs['increment_ok'] = kwargs['num_unconfirmed'] == 0
        return super().get_context_data(**kwargs)


class RoundIncrementView(SuperuserRequiredMixin, PostOnlyRedirectView, RoundMixin):

    def post(self, request, *args, **kwargs):
        self.get_tournament().advance_round()
        return redirect_round('availability_index', self.get_tournament().current_round)


class BlankSiteStartView(FormView):
    """This view is presented to the user when there are no tournaments and no
    user accounts. It prompts the user to create a first superuser. It rejects
    all requests, GET or POST, if there exists any user account in the
    system."""

    form_class = SuperuserCreationForm
    template_name = "blank_site_start.html"
    lock = Lock()
    success_url = reverse_lazy('tabbycat-index')

    def get(self, request):
        if User.objects.exists():
            logger.error("Tried to get the blank-site-start view when a user account already exists.")
            return redirect('tabbycat-index')

        return super().get(request)

    def post(self, request):
        with self.lock:
            if User.objects.exists():
                logger.error("Tried to post the blank-site-start view when a user account already exists.")
                messages.error(request, "Whoops! It looks like someone's already created the first user account. Please log in.")
                return redirect('login')

            return super().post(request)

    def form_valid(self, form):
        form.save()
        user = authenticate(username=self.request.POST['username'], password=self.request.POST['password1'])
        login(self.request, user)
        messages.success(self.request, "Welcome! You've created an account for %s." % user.username)

        return super().form_valid(form)


class CreateTournamentView(SuperuserRequiredMixin, CreateView):
    """This view allows a logged-in superuser to create a new tournament."""

    model = Tournament
    form_class = TournamentForm
    template_name = "create_tournament.html"


class TournamentPermanentRedirectView(RedirectView):
    """Redirect old-style /t/<slug>/... URLs to new-style /<slug>/... URLs."""

    url = "/%(slug)s/%(page)s"
    permanent = True

    def get_redirect_url(self, *args, **kwargs):
        slug = kwargs['slug']
        if not Tournament.objects.filter(slug=slug).exists():
            logger.error("Tried to redirect non-existent tournament slug '%s'" % slug)
            raise Http404("There isn't a tournament with slug '%s'." % slug)
        return super().get_redirect_url(*args, **kwargs)