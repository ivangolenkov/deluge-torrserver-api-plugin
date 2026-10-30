from types import SimpleNamespace

from twisted.internet import defer
from twisted.web.server import NOT_DONE_YET

from deluge_stream.torrserver import API


class Request:
    method = b"GET"
    finished = False

    def __init__(self):
        self.notifications = []

    def setHeader(self, *args):
        pass

    def setResponseCode(self, code):
        self.code = code

    def notifyFinish(self):
        # Match Twisted: registering after finish does NOT replay the event.
        result = defer.Deferred()
        self.notifications.append(result)
        return result

    def finish(self):
        self.finished = True
        for notification in self.notifications:
            notification.callback(None)


def test_synchronous_producer_finish_does_not_exhaust_http_admission():
    server = SimpleNamespace(closed=False, active_requests=set())
    api = API(server)

    def dispatch(request):
        request.finish()
        return NOT_DONE_YET

    api._dispatch = dispatch
    for _ in range(40):
        request = Request()
        assert api.render(request) is NOT_DONE_YET
        assert request.finished
        assert not server.active_requests


def test_synchronous_disconnect_cancels_dispatch_returned_after_finish():
    server = SimpleNamespace(closed=False, active_requests=set())
    api = API(server)
    pending = defer.Deferred()

    def dispatch(request):
        request.finish()
        return pending

    api._dispatch = dispatch
    api.render(Request())
    assert pending.called
    assert not server.active_requests
