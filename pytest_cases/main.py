# Use true division operator always even in old python 2.x (used in `_get_case_getter_s`)
from __future__ import division

import sys
from abc import abstractmethod, ABCMeta
from collections import OrderedDict
from distutils.version import LooseVersion
from inspect import getmembers, isgeneratorfunction, getmodule

from decopatch import function_decorator, DECORATED, with_parenthesis
from makefun import with_signature, add_signature_parameters, remove_signature_parameters

try:  # python 3.3+
    from inspect import signature, Parameter
except ImportError:
    from funcsigs import signature, Parameter

from pytest_cases.common import yield_fixture, get_pytest_parametrize_marks

try:  # type hints, python 3+
    from typing import Callable, Union, Optional, Any, Tuple, List, Dict, Iterable

    from pytest_cases.case_funcs import CaseData, ExpectedError

    from types import ModuleType

    # Type hint for the simple functions
    CaseFunc = Callable[[], CaseData]

    # Type hint for generator functions
    GeneratedCaseFunc = Callable[[Any], CaseData]

except ImportError:
    pass

# noinspection PyBroadException
from warnings import warn

import six

from pytest_cases.case_funcs import _GENERATOR_FIELD, CASE_TAGS_FIELD

try:
    from typing import Type
except ImportError:
    # on old versions of typing module the above does not work. Since our code below has all Type hints quoted it's ok
    pass

import pytest


class CaseDataGetter(six.with_metaclass(ABCMeta)):
    """
    A proxy for a test case. Instances of this class are created by `@cases_data` or `get_all_cases`.

    It provides a single method: `get(self, *args, **kwargs) -> CaseData`
    This method calls the actual underlying case with arguments propagation, and returns the result.

    The case functions can use the proposed standard `CaseData` type hint and return outputs matching this type hint,
    but this is not mandatory.
    """
    @abstractmethod
    def get(self, *args, **kwargs):
        # type: (...) -> Union[CaseData, Any]
        """
        Retrieves the contents of the test case, with the provided arguments.
        :return:
        """

    def get_marks(self):
        """
        Returns the pytest marks on this case, if any
        :return:
        """
        return []

    def get_for(self, key):
        # type: (...) -> CaseData
        """
        DEPRECATED as it is hardcoded for a very particular format of case data. Please rather use get() directly, and
        do the selection in the results yourself based on your case data format.
        ---
        Returns a new case data getter where the data is automatically filtered with the key.
        This only works if the function returns a `MultipleStepsCaseData`
        :return:
        """
        warn("This method is deprecated, as it is hardcoded for a very particular format of case data. Please rather"
             "use get() directly, and do the selection in the results yourself based on your case data format",
             category=DeprecationWarning, stacklevel=2)

        data = self.get()

        # assume that the data is a MultiStepsCaseData = a tuple with 3 items and the second and third are dict or None
        ins = data[0]
        outs = None if data[1] is None else data[1][key]
        err = None if data[2] is None else data[2][key]

        return ins, outs, err


class CaseDataFromFunction(CaseDataGetter):
    """
    A CaseDataGetter relying on a function
    """

    def __init__(self, data_generator_func,  # type: Union[CaseFunc, GeneratedCaseFunc]
                 case_name=None,             # type: str
                 function_kwargs=None        # type: Dict[str, Any]
                 ):
        """

        :param data_generator_func:
        """
        self.f = data_generator_func
        self.case_name = case_name
        if function_kwargs is None:
            function_kwargs = dict()
        self.function_kwargs = function_kwargs

    def __str__(self):
        if self.case_name is not None:
            return self.case_name
        else:
            return self.f.__name__

    def __repr__(self):
        return "Test Case Data generator - [" + str(self) + "] - " + str(self.f)

    def get_marks(self):
        """
        Overrides default implementation to return the marks that are on the case function
        :return:
        """
        return get_pytest_marks_on_case_func(self.f)

    def get(self, *args, **kwargs):
        # type: (...) -> Union[CaseData, Any]
        """
        This implementation relies on the inner function to generate the case data.
        :return:
        """
        kwargs.update(self.function_kwargs)
        return self.f(*args, **kwargs)


def get_pytest_marks_on_case_func(f):
    """
    Utility to return all pytest marks applied on a case function

    :param f:
    :return:
    """
    try:
        return f.pytestmark
    except AttributeError:
        try:
            # old pytest < 3: marks are set as fields on the function object
            # but they do not have a particulat type, their type is 'instance'...
            return [v for v in vars(f).values() if str(v).startswith("<MarkInfo '")]
        except AttributeError:
            return []


CASE_PREFIX = 'case_'
"""Prefix used by default to identify case functions within a module"""

THIS_MODULE = object()
"""Marker that can be used instead of a module name to indicate that the module is the current one"""


@function_decorator
def cases_fixture(cases=None,                       # type: Union[Callable[[Any], Any], Iterable[Callable[[Any], Any]]]
                  module=None,                      # type: Union[ModuleType, Iterable[ModuleType]]
                  case_data_argname='case_data',    # type: str
                  has_tag=None,                     # type: Any
                  filter=None,                      # type: Callable[[List[Any]], bool]
                  f=DECORATED,
                  **kwargs
                  ):
    """
    DEPRECATED - use double annotation `@pytest_fixture_plus` + `@cases_data` instead

    ```python
    @pytest_fixture_plus
    @cases_data(module=xxx)
    def my_fixture(case_data)
    ```

    Decorates a function so that it becomes a parametrized fixture.

    The fixture will be automatically parametrized with all cases listed in module `module`, or with
    all cases listed explicitly in `cases`.

    Using it with a non-None `module` argument is equivalent to
     * extracting all cases from `module`
     * then decorating your function with @pytest.fixture(params=cases) with all the cases

    So

    ```python
    from pytest_cases import cases_fixture, CaseData

    # import the module containing the test cases
    import test_foo_cases

    @cases_fixture(module=test_foo_cases)
    def foo_fixture(case_data: CaseData):
        ...
    ```

    is equivalent to:

    ```python
    import pytest
    from pytest_cases import get_all_cases, CaseData

    # import the module containing the test cases
    import test_foo_cases

    # manually list the available cases
    cases = get_all_cases(module=test_foo_cases)

    # parametrize the fixture manually
    @pytest.fixture(params=cases)
    def foo_fixture(request):
        case_data = request.param  # type: CaseData
        ...
    ```

    Parameters (cases, module, has_tag, filter) can be used to perform explicit listing, or filtering. See
    `get_all_cases()` for details.

    :param cases: a single case or a hardcoded list of cases to use. Only one of `cases` and `module` should be set.
    :param module: a module or a hardcoded list of modules to use. You may use `THIS_MODULE` to indicate that the
        module is the current one. Only one of `cases` and `module` should be set.
    :param case_data_argname: the optional name of the function parameter that should receive the `CaseDataGetter`
        object. Default is 'case_data'.
    :param has_tag: an optional tag used to filter the cases. Only cases with the given tag will be selected. Only
        cases with the given tag will be selected.
    :param filter: an optional filtering function taking as an input a list of tags associated with a case, and
        returning a boolean indicating if the case should be selected. It will be used to filter the cases in the
        `module`. It both `has_tag` and `filter` are set, both will be applied in sequence.
    :return:
    """
    # apply @cases_data (that will translate to a @pytest.mark.parametrize)
    parametrized_f = cases_data(cases=cases, module=module,
                                case_data_argname=case_data_argname, has_tag=has_tag, filter=filter)(f)
    # apply @pytest_fixture_plus
    return pytest_fixture_plus(**kwargs)(parametrized_f)


@function_decorator
def pytest_fixture_plus(scope="function",
                        params=None,
                        autouse=False,
                        ids=None,
                        name=None,
                        fixture_func=DECORATED,
                        **kwargs):
    """ decorator to mark a fixture factory function.

    Identical to `@pytest.fixture` decorator, except that it supports multi-parametrization with
    `@pytest.mark.parametrize` as requested in https://github.com/pytest-dev/pytest/issues/3960.

    :param scope: the scope for which this fixture is shared, one of
                "function" (default), "class", "module" or "session".
    :param params: an optional list of parameters which will cause multiple
                invocations of the fixture function and all of the tests
                using it.
    :param autouse: if True, the fixture func is activated for all tests that
                can see it.  If False (the default) then an explicit
                reference is needed to activate the fixture.
    :param ids: list of string ids each corresponding to the params
                so that they are part of the test id. If no ids are provided
                they will be generated automatically from the params.
    :param name: the name of the fixture. This defaults to the name of the
                decorated function. If a fixture is used in the same module in
                which it is defined, the function name of the fixture will be
                shadowed by the function arg that requests the fixture; one way
                to resolve this is to name the decorated function
                ``fixture_<fixturename>`` and then use
                ``@pytest.fixture(name='<fixturename>')``.
    :param kwargs: other keyword arguments for `@pytest.fixture`
    """
    # Compatibility for the 'name' argument
    if LooseVersion(pytest.__version__) >= LooseVersion('3.0.0'):
        # pytest version supports "name" keyword argument
        kwargs['name'] = name
    elif name is not None:
        # 'name' argument is not supported in this old version, use the __name__ trick.
        fixture_func.__name__ = name

    # Collect all @pytest.mark.parametrize markers (including those created by usage of @cases_data)
    parametrizer_marks = get_pytest_parametrize_marks(fixture_func)

    # the module will be used to add fixtures dynamically
    module = getmodule(fixture_func)

    # for each dependency create an associated "param" fixture
    # Note: we could instead have created a huge parameter containing all parameters...
    # Pros = no additional fixture.
    # Cons: less readable and ids would be difficult to create
    params_map = OrderedDict()
    for m in parametrizer_marks:
        # check what the mark specifies in terms of parameters
        if len(m.param_names) < 1:
            raise ValueError("Fixture function '%s' decorated with '@pytest_fixture_plus' has an empty parameter "
                             "name in a @pytest.mark.parametrize mark")

        else:
            paramnames = [name.strip() for name in m.param_names]

            # create a fixture function for this parameter
            def _param_fixture(request):
                """a dummy fixture that simply returns the parameter"""
                return request.param

            # generate a fixture name (find an available name if already used)
            gen_name = fixture_func.__name__ + "__" + 'X'.join(paramnames)  # + "__gen"
            i = 0
            _param_fixture.__name__ = gen_name
            while _param_fixture.__name__ in dir(module):
                i += 1
                _param_fixture.__name__ = gen_name + '_' + str(i)

            # create the fixture with param name, values and ids, and with same scope than requesting func.
            param_fixture = pytest.fixture(scope=scope, params=m.param_values, ids=m.param_ids)(_param_fixture)

            # Add the fixture dynamically: we have to add it to the function holder module as explained in
            # https://github.com/pytest-dev/pytest/issues/2424
            if _param_fixture.__name__ not in dir(module):
                setattr(module, _param_fixture.__name__, param_fixture)
            else:
                raise ValueError("The {} fixture automatically generated by `@pytest_fixture_plus` already exists in "
                                 "module {}. This should not happen given the automatic name generation"
                                 "".format(_param_fixture.__name__, module))

            # remember
            params_map[_param_fixture.__name__] = paramnames

    # wrap the fixture function so that each of its parameter becomes the associated fixture name
    new_parameter_names = tuple(params_map.keys())
    old_parameter_names = tuple(v for l in params_map.values() for v in l)

    # common routine used below. Fills kwargs with the appropriate names and values from fixture_params
    def _get_arguments(*args, **kwargs):
        # kwargs contains the generated fixture names so we have to remove them.
        # For each generated fixture, there are one or several parameters to inject in kwargs
        i = 0
        for new_p_name in new_parameter_names:
            fixture_param_value = kwargs.pop(new_p_name)
            if len(params_map[new_p_name]) == 1:
                # a single parameter for that generated ficture (@pytest.mark.parametrize with a single name)
                kwargs[params_map[new_p_name][0]] = fixture_param_value
                i += 1
            else:
                # several parameters for that generated fixture (@pytest.mark.parametrize with several names)
                # unpack all of them and inject them in the kwargs
                for old_p_name, old_p_value in zip(params_map[new_p_name], fixture_param_value):
                    kwargs[old_p_name] = old_p_value
                    i += 1

        return args, kwargs

    # create the new signature that we want to expose to pytest
    old_sig = signature(fixture_func)
    new_sig = remove_signature_parameters(old_sig, *old_parameter_names)
    # add them in reversed order so as to match the same test order than in pytest.
    new_sig = add_signature_parameters(new_sig, first=tuple(Parameter(n, kind=Parameter.POSITIONAL_OR_KEYWORD)
                                                            for n in reversed(new_parameter_names)))

    # Finally create the fixture function, a wrapper of user-provided fixture with the new signature
    if not isgeneratorfunction(fixture_func):
        # normal function with return statement
        @with_signature(new_sig)
        def wrapped_fixture_func(*args, **kwargs):
            args, kwargs = _get_arguments(*args, **kwargs)
            return fixture_func(*args, **kwargs)

        # transform the created wrapper into a fixture
        fixture_decorator = pytest.fixture(scope=scope, params=params, autouse=autouse, ids=ids, **kwargs)
        return fixture_decorator(wrapped_fixture_func)

    else:
        # generator function (with a yield statement)
        @with_signature(new_sig)
        def wrapped_fixture_func(*args, **kwargs):
            args, kwargs = _get_arguments(*args, **kwargs)
            for res in fixture_func(*args, **kwargs):
                yield res

        # transform the created wrapper into a fixture
        fixture_decorator = yield_fixture(scope=scope, params=params, autouse=autouse, ids=ids, **kwargs)
        return fixture_decorator(wrapped_fixture_func)


@function_decorator(custom_disambiguator=with_parenthesis)
def cases_data(cases=None,                       # type: Union[Callable[[Any], Any], Iterable[Callable[[Any], Any]]]
               module=None,                      # type: Union[ModuleType, Iterable[ModuleType]]
               case_data_argname='case_data',    # type: str
               has_tag=None,                     # type: Any
               filter=None,                      # type: Callable[[List[Any]], bool]
               test_func=DECORATED,
               ):
    """
    Decorates a test function so as to automatically parametrize it with all cases listed in module `module`, or with
    all cases listed explicitly in `cases`.

    Using it with a non-None `module` argument is equivalent to
     * extracting all cases from `module`
     * then decorating your function with @pytest.mark.parametrize with all the cases

    So

    ```python
    from pytest_cases import cases_data, CaseData

    # import the module containing the test cases
    import test_foo_cases

    @cases_data(module=test_foo_cases)
    def test_foo(case_data: CaseData):
        ...
    ```

    is equivalent to:

    ```python
    import pytest
    from pytest_cases import get_all_cases, CaseData

    # import the module containing the test cases
    import test_foo_cases

    # manually list the available cases
    cases = get_all_cases(module=test_foo_cases)

    # parametrize the test function manually
    @pytest.mark.parametrize('case_data', cases, ids=str)
    def test_foo(case_data: CaseData):
        ...
    ```

    Parameters (cases, module, has_tag, filter) can be used to perform explicit listing, or filtering. See
    `get_all_cases()` for details.

    :param cases: a single case or a hardcoded list of cases to use. Only one of `cases` and `module` should be set.
    :param module: a module or a hardcoded list of modules to use. You may use `THIS_MODULE` to indicate that the
        module is the current one. Only one of `cases` and `module` should be set.
    :param case_data_argname: the optional name of the function parameter that should receive the `CaseDataGetter`
        object. Default is 'case_data'.
    :param has_tag: an optional tag used to filter the cases. Only cases with the given tag will be selected. Only
        cases with the given tag will be selected.
    :param filter: an optional filtering function taking as an input a list of tags associated with a case, and
        returning a boolean indicating if the case should be selected. It will be used to filter the cases in the
        `module`. It both `has_tag` and `filter` are set, both will be applied in sequence.
    :return:
    """
    # equivalent to @mark.parametrize('case_data', cases) where cases is a tuple containing a CaseDataGetter for

    # First list all cases according to user preferences
    _cases = get_all_cases(cases, module, test_func, has_tag, filter)

    # Then transform into required arguments for pytest (applying the pytest marks if needed)
    marked_cases, cases_ids = get_pytest_parametrize_args(_cases)

    # Finally create the pytest decorator and apply it
    parametrizer = pytest.mark.parametrize(case_data_argname, marked_cases, ids=cases_ids)

    return parametrizer(test_func)


def get_pytest_parametrize_args(cases):
    """
    Transforms a list of cases into a tuple containing the arguments to use in `@pytest.mark.parametrize`
    the tuple is (marked_cases, ids) where

     - marked_cases is a list containing either the case or a pytest-marked case (using the pytest marks that were
     present on the case function)
     - ids is a list containing the case ids to use as test ids.

    :param cases:
    :return: (marked_cases, ids)
    """
    # hardcode the case ids, as simply passing 'ids=str' would not work when cases are marked cases
    case_ids = [str(c) for c in cases]

    # create the pytest parameter values with the appropriate pytest marks
    marked_cases = [c if len(c.get_marks()) == 0 else get_marked_parameter_for_case(c, marks=c.get_marks())
                    for c in cases]

    return marked_cases, case_ids


# Compatibility for the way we put marks on single parameters in the list passed to @pytest.mark.parametrize
# see https://docs.pytest.org/en/3.3.0/skipping.html?highlight=mark%20parametrize#skip-xfail-with-parametrize

try:
    # check if pytest.param exists
    _ = pytest.param
except AttributeError:
    # if not this is how it was done
    # see e.g. https://docs.pytest.org/en/2.9.2/skipping.html?highlight=mark%20parameter#skip-xfail-with-parametrize
    def get_marked_parameter_for_case(c, marks):
        if len(marks) > 1:
            raise ValueError("Multiple marks on parameters not supported for old versions of pytest")
        else:
            # get a decorator for each of the markinfo
            marks_mod = transform_marks_into_decorators(marks)

            # decorate
            return marks_mod[0](c)
else:
    # Otherise pytest.param exists, it is easier
    def get_marked_parameter_for_case(c, marks):
        # get a decorator for each of the markinfo
        marks_mod = transform_marks_into_decorators(marks)

        # decorate
        return pytest.param(c, marks=marks_mod)


def transform_marks_into_decorators(marks):
    """
    Transforms the provided marks (MarkInfo) obtained from marked cases, into MarkDecorator so that they can
    be re-applied to generated pytest parameters in the global @pytest.mark.parametrize.

    :param marks:
    :return:
    """
    marks_mod = []
    try:
        for m in marks:
            md = pytest.mark.MarkDecorator()
            if LooseVersion(pytest.__version__) >= LooseVersion('3.0.0'):
                 md.mark = m
            else:
                md.name = m.name
                # md.markname = m.name
                md.args = m.args
                md.kwargs = m.kwargs

            # markinfodecorator = getattr(pytest.mark, markinfo.name)
            # markinfodecorator(*markinfo.args)

            marks_mod.append(md)
    except Exception as e:
        warn("Caught exception while trying to mark case: [%s] %s" % (type(e), e))
    return marks_mod


def get_all_cases(cases=None,               # type: Union[Callable[[Any], Any], Iterable[Callable[[Any], Any]]]
                  module=None,              # type: Union[ModuleType, Iterable[ModuleType]]
                  this_module_object=None,  # type: Any
                  has_tag=None,             # type: Any
                  filter=None               # type: Callable[[List[Any]], bool]
                  ):
    # type: (...) -> List[CaseDataGetter]
    """
    Lists all desired cases from the user inputs. This function may be convenient for debugging purposes.

    :param cases: a single case or a hardcoded list of cases to use. Only one of `cases` and `module` should be set.
    :param module: a module or a hardcoded list of modules to use. You may use `THIS_MODULE` to indicate that the
        module is the current one. Only one of `cases` and `module` should be set.
    :param this_module_object: any variable defined in the module of interest, for example a function. It is used to
        find "this module", when `module` contains `THIS_MODULE`.
    :param has_tag: an optional tag used to filter the cases. Only cases with the given tag will be selected. Only
        cases with the given tag will be selected.
    :param filter: an optional filtering function taking as an input a list of tags associated with a case, and
        returning a boolean indicating if the case should be selected. It will be used to filter the cases in the
        `module`. It both `has_tag` and `filter` are set, both will be applied in sequence.
    :return:
    """
    if module is not None and cases is not None:
        raise ValueError("Only one of module and cases should be provided")
    elif module is None:
        # Hardcoded sequence of cases, or single case
        if callable(cases):
            # single element
            _cases = [case_getter for case_getter in _get_case_getter_s(cases)]
        else:
            # already a sequence
            _cases = [case_getter for c in cases for case_getter in _get_case_getter_s(c)]
    else:
        # Gather all cases from the reference module(s)
        try:
            _cases = []
            for m in module:
                m = sys.modules[this_module_object.__module__] if m is THIS_MODULE else m
                _cases += extract_cases_from_module(m, has_tag=has_tag, filter=filter)
        except TypeError:
            # 'module' object is not iterable: a single module was provided
            m = sys.modules[this_module_object.__module__] if module is THIS_MODULE else module
            _cases = extract_cases_from_module(m, has_tag=has_tag, filter=filter)

    return _cases


def _get_code(f):
    """
    Returns the source code associated to function f. It is robust to wrappers such as @lru_cache
    :param f:
    :return:
    """
    if hasattr(f, '__wrapped__'):
        return _get_code(f.__wrapped__)
    elif hasattr(f, '__code__'):
        return f.__code__
    else:
        raise ValueError("Cannot get code information for function " + str(f))


def extract_cases_from_module(module,        # type: ModuleType
                              has_tag=None,  # type: Any
                              filter=None    # type: Callable[[List[Any]], bool]
                              ):
    # type: (...) -> List[CaseDataGetter]
    """
    Internal method used to create a list of `CaseDataGetter` for all cases available from the given module.
    See `@cases_data`

    :param module:
    :param has_tag: a tag used to filter the cases. Only cases with the given tag will be selected
    :param filter: a function taking as an input a list of tags associated with a case, and returning a boolean
        indicating if the case should be selected
    :return:
    """
    if filter is not None and not callable(filter):
        raise ValueError("`filter` should be a callable starting in pytest-cases 0.8.0. If you wish to provide a single"
                         " tag to match, use `has_tag` instead.")

    # First gather all case data providers in the reference module
    cases_dct = dict()
    for f_name, f in getmembers(module, callable):
        # only keep the functions
        #  - from the module file (not the imported ones),
        #  - starting with prefix 'case_'
        if f_name.startswith(CASE_PREFIX):
            code = _get_code(f)
            # check if the function is actually defined in this module (not imported)
            if code.co_filename == module.__file__:  # or we could use f.__module__ == module.__name__ ?
                #  - with the optional filter/tag
                _tags = getattr(f, CASE_TAGS_FIELD, ())

                selected = True  # by default select the case, then AND the conditions
                if has_tag is not None:
                    selected = selected and (has_tag in _tags)
                if filter is not None:
                    selected = selected and filter(_tags)

                if selected:
                    # update the dictionary with the case getters
                    _get_case_getter_s(f, code, cases_dct)

    # convert into a list, taking all cases in order of appearance in the code (sort by source code line number)
    cases = [cases_dct[k] for k in sorted(cases_dct.keys())]

    return cases


def _get_case_getter_s(f,
                       f_code=None,
                       cases_dct=None):
    # type: (...) -> Optional[List[CaseDataFromFunction]]
    """
    Creates the case function getter or the several cases function getters (in case of a generator) associated with
    function f. If cases_dct is provided, they are stored in this dictionary with a key equal to their code line number.
    For generated cases, a floating line number is created to preserve order.

    :param f:
    :param f_code: should be provided if cases_dct is provided.
    :param cases_dct: an optional dictionary where to store the created function wrappers
    :return:
    """

    # create a return variable if needed
    if cases_dct is None:
        cases_list = []
    else:
        cases_list = None

    # Handle case generators
    gen = getattr(f, _GENERATOR_FIELD, False)
    if gen:
        already_used_names = []

        names, param_ids, all_param_values_combinations = gen

        if isinstance(names, str):
            # then this is a string formatter creating the names
            _formatter = names
            def names(**params):
                return _formatter.format(**params)

        nb_cases_generated = len(all_param_values_combinations)
        if not callable(names):
            if len(names) != nb_cases_generated:
                raise ValueError("An explicit list of names has been provided but it has not the same length (%s) than"
                                 " the number of cases to be generated (%s)" % (len(names), nb_cases_generated))

        for gen_case_id, case_params_values in enumerate(all_param_values_combinations):
            # build the dictionary of parameters for the case functions
            gen_case_params_dct = dict(zip(param_ids, case_params_values))

            # generate the case name by applying the name template
            if callable(names):
                gen_case_name = names(**gen_case_params_dct)
            else:
                # an explicit list is provided
                gen_case_name = names[gen_case_id]

            if gen_case_name in already_used_names:
                raise ValueError("Generated function names for generator case function {} are not "
                                 "unique. Please use all parameter names in the string format variables"
                                 "".format(f.__name__))
            else:
                already_used_names.append(gen_case_name)
            case_getter = CaseDataFromFunction(f, gen_case_name, gen_case_params_dct)

            # save the result in the list or the dict
            if cases_dct is None:
                cases_list.append(case_getter)
            else:
                # with an artificial floating point line number to keep order in dict
                gen_line_nb = f_code.co_firstlineno + (gen_case_id / nb_cases_generated)
                cases_dct[gen_line_nb] = case_getter
    else:
        # single case
        case_getter = CaseDataFromFunction(f)

        # save the result
        if cases_dct is None:
            cases_list.append(case_getter)
        else:
            cases_dct[f_code.co_firstlineno] = case_getter

    if cases_dct is None:
        return cases_list


def unfold_expected_err(expected_e  # type: ExpectedError
                        ):
    # type: (...) -> Tuple[Optional['Type[Exception]'], Optional[Exception], Optional[Callable[[Exception], bool]]]
    """
    'Unfolds' the expected error `expected_e` to return a tuple of
     - expected error type
     - expected error instance
     - error validation callable

    If `expected_e` is an exception type, returns `expected_e, None, None`
    If `expected_e` is an exception instance, returns `type(expected_e), expected_e, None`
    If `expected_e` is an exception validation function, returns `Exception, None, expected_e`

    :param expected_e: an `ExpectedError`, that is, either an exception type, an exception instance, or an exception
        validation function
    :return:
    """
    if type(expected_e) is type and issubclass(expected_e, Exception):
        return expected_e, None, None

    elif issubclass(type(expected_e), Exception):
        return type(expected_e), expected_e, None

    elif callable(expected_e):
        return Exception, None, expected_e

    raise ValueError("ExpectedNormal error should either be an exception type, an exception instance, or an exception "
                     "validation callable")
