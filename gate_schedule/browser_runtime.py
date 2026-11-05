"""Run the existing workspace endpoints without an HTTP server or database.

The worker receives a document snapshot while holding the browser's cross-tab
write lock. Its proposed snapshot becomes visible only after durable browser
storage commits successfully. All domain validation and Excel routes are shared
with the desktop application.
"""
import base64
from copy import deepcopy
import inspect
import json
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Match, Route

from .group_workspaces import GroupWorkspaces
from .schedule_runs import RunControl
from .workspace_api import attach_workspace


class BrowserStore:
    def __init__(self, documents=None):
        self.documents = deepcopy(documents or {})

    def read_document(self, key):
        return deepcopy(self.documents.get(key))

    def compare_and_save(self, key, expected_revision, state):
        current = self.documents.get(key, {'revision': 0})
        if current['revision'] != expected_revision:
            raise ValueError('数据已经更新，请重新读取；当前输入未被覆盖')
        self.documents[key] = deepcopy(state)


class BrowserRoutes:
    """Collect desktop endpoint functions without starting an ASGI server."""
    def __init__(self):
        self.routes = []

    def _register(self, method, path):
        def decorate(endpoint):
            self.routes.append(Route(path, endpoint, methods=[method]))
            return endpoint
        return decorate

    def get(self, path):
        return self._register('GET', path)

    def post(self, path):
        return self._register('POST', path)

    def include_router(self, router):
        self.routes.extend(router.routes)


class BrowserControl(RunControl):
    def __init__(self):
        super().__init__()
        from gate_browser_js import progress, stopped, keeping
        self._progress, self._stopped, self._keeping = progress, stopped, keeping

    def sync_stop(self):
        if self._stopped():
            self.keep = self._keeping()
            self.stopped.set()

    def stage(self, label):
        self.sync_stop()
        super().stage(label)
        self.publish()

    def solution(self, scenario, assignments):
        super().solution(scenario, assignments)
        self.publish()
        self.sync_stop()
        if self.stopped.is_set() and self.solver:
            self.solver.stop_search()

    def publish(self):
        self._progress(json.dumps({**self.view, **self.progress()}))

    def finish(self, commit):
        self.sync_stop()
        return super().finish(commit)


async def _execute(function, *args):
    return function(*args)


async def request(raw):
    data = json.loads(raw)
    store = BrowserStore(data.get('documents'))
    groups = GroupWorkspaces(Path('/browser-unused'), store=store)
    app = BrowserRoutes()
    attach_workspace(app, Path('/browser-unused'), groups=groups, execute_task=_execute)
    url = urlsplit(data['url'])
    method = data.get('method', 'GET')
    body = base64.b64decode(data.get('body', ''))
    scope = {'type': 'http', 'http_version': '1.1', 'method': method,
             'path': url.path, 'root_path': '', 'scheme': 'https',
             'query_string': url.query.encode(),
             'headers': [(k.lower().encode(), v.encode()) for k, v in data.get('headers', {}).items()]}

    async def receive():
        return {'type': 'http.request', 'body': body, 'more_body': False}

    try:
        # Runs use the same Workbench pipeline and RunControl validator without
        # Python threads; the JS coordinator owns polling and stop requests.
        if data.get('run'):
            group = url.path.split('/')[3]
            command = json.loads(body)
            result = groups.workbench(group).execute(command['expectedRevision'], command['action'],
                command.get('payload', {}), command.get('planId'), control=BrowserControl())
        else:
            result = None
            for route in app.routes:
                match, child = route.matches(scope)
                if match != Match.FULL:
                    continue
                kwargs = dict(child.get('path_params', {}))
                query = parse_qs(url.query)
                for name, parameter in inspect.signature(route.endpoint).parameters.items():
                    if name == 'request':
                        kwargs[name] = Request(scope, receive)
                    elif name in query:
                        value = query[name][0]
                        kwargs[name] = int(value) if name in ('slot', 'expectedRevision', 'expectedGroupsRevision') else value
                    elif name not in kwargs and parameter.default is inspect.Parameter.empty:
                        raise HTTPException(422, f'缺少参数：{name}')
                result = route.endpoint(**kwargs)
                if inspect.isawaitable(result):
                    result = await result
                break
            else:
                raise HTTPException(404, '接口不存在')
        if isinstance(result, Response):
            response = {'status': result.status_code, 'headers': dict(result.headers),
                        'body': base64.b64encode(result.body).decode()}
        else:
            response = _json_response(200, result)
    except HTTPException as error:
        response = _json_response(error.status_code, {'detail': error.detail})
    except (ValueError, KeyError, TypeError) as error:
        response = _json_response(422, {'detail': str(error)})
    return json.dumps({'response': response, 'documents': store.documents}, ensure_ascii=False)


def _json_response(status, data):
    return {'status': status, 'headers': {'content-type': 'application/json; charset=utf-8'},
            'body': base64.b64encode(json.dumps(data, ensure_ascii=False).encode()).decode()}


def request_sync(raw):
    """Drive in-memory route coroutines on a JSPI-enabled Python stack.

    The browser transport does no network/thread I/O inside a route. CP-SAT
    suspends this stack through run_sync; unexpected async I/O fails explicitly.
    """
    coroutine = request(raw)
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    finally:
        coroutine.close()
    raise RuntimeError('浏览器接口发生了不支持的异步操作')
