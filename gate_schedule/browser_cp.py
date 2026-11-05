"""The CP-SAT surface used by this product, executed by the browser WASM build.

This adapter serializes integer expressions, never scheduling rules. Decimal
strings preserve int64 values across JavaScript's number precision boundary.
Unsupported operations fail explicitly instead of weakening a model.
"""
import json
from types import SimpleNamespace

UNKNOWN, MODEL_INVALID, FEASIBLE, INFEASIBLE, OPTIMAL = 0, 1, 2, 3, 4


class LinearExpr:
    def __init__(self, terms=None, offset=0):
        self.terms, self.offset = terms or {}, offset

    def __add__(self, other):
        other = _expr(other)
        terms = self.terms.copy()
        for index, coefficient in other.terms.items():
            terms[index] = terms.get(index, 0) + coefficient
        return LinearExpr({i: c for i, c in terms.items() if c}, self.offset + other.offset)

    __radd__ = __add__

    def __neg__(self):
        return self * -1

    def __sub__(self, other):
        return self + -_expr(other)

    def __rsub__(self, other):
        return _expr(other) + -self

    def __mul__(self, coefficient):
        if type(coefficient) is not int:
            raise TypeError('CP-SAT仅接受整数线性系数')
        return LinearExpr({i: c * coefficient for i, c in self.terms.items()}, self.offset * coefficient)

    __rmul__ = __mul__

    def __eq__(self, other):
        return ('eq', self - other)

    def __le__(self, other):
        return ('le', self - other)

    def __ge__(self, other):
        return ('ge', self - other)

    def __bool__(self):
        raise TypeError('线性表达式不能转为布尔值')

    def data(self):
        return {'terms': [[i, str(c)] for i, c in self.terms.items()], 'offset': str(self.offset)}


def _expr(value):
    if isinstance(value, LinearExpr):
        return value
    if type(value) is int:
        return LinearExpr(offset=value)
    raise TypeError(f'不支持的CP-SAT表达式：{type(value).__name__}')


class IntVar(LinearExpr):
    def __init__(self, index):
        super().__init__({index: 1})


class CpModel:
    def __init__(self):
        self.variables, self.constraints = [], []
        self.objective = _expr(0)

    def new_bool_var(self, name):
        return self.new_int_var(0, 1, name)

    def new_int_var(self, lo, hi, name):
        var = IntVar(len(self.variables))
        self.variables.append([str(lo), str(hi), name])
        return var

    def add(self, constraint):
        if type(constraint) is bool:
            self.constraints.append(['bool', constraint])
        elif isinstance(constraint, tuple) and constraint[0] in ('eq', 'le', 'ge'):
            self.constraints.append([constraint[0], constraint[1].data()])
        else:
            raise TypeError('不支持的CP-SAT约束')

    def add_max_equality(self, target, expressions):
        self.constraints.append(['max', _expr(target).data(), [_expr(e).data() for e in expressions]])

    def add_min_equality(self, target, expressions):
        self.constraints.append(['min', _expr(target).data(), [_expr(e).data() for e in expressions]])

    def add_element(self, index, expressions, target):
        self.constraints.append(['element', _expr(target).data(), [_expr(e).data() for e in expressions], _expr(index).data()])

    def minimize(self, objective):
        self.objective = _expr(objective)


class CpSolverSolutionCallback:
    def value(self, expression):
        return _value(expression, self._values)


def _value(expression, values):
    expression = _expr(expression)
    return expression.offset + sum(c * values[i] for i, c in expression.terms.items())


class CpSolver:
    def __init__(self):
        self.parameters = SimpleNamespace()
        self._values = []
        self._stop = False

    def solve(self, model, callback=None):
        from pyodide.ffi import run_sync
        from gate_browser_js import solve_cp

        def solution(raw):
            if callback is not None:
                callback._values = [int(v) for v in json.loads(raw)]
                callback.on_solution_callback()
            return self._stop

        raw = run_sync(solve_cp(json.dumps({
            'variables': model.variables, 'constraints': model.constraints,
            'objective': model.objective.data(), 'parameters': vars(self.parameters),
        }), solution))
        response = json.loads(raw)
        self._values = [int(v) for v in response.get('solution', [])]
        return response['status']

    def value(self, expression):
        return _value(expression, self._values)

    def stop_search(self):
        from gate_browser_js import interrupt_cp
        self._stop = True
        interrupt_cp()
