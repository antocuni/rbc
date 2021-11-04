"""
To run:

  $ cd /tmp/
  $ git clone git@github.com:data-apis/array-api-tests.git
  $ cd /path/to/rbc/utils
  $ ln -s /tmp/array-api-tests/array-api-tests/function_stubs .
  $ python generate_api_tables.py

The result is saved to /tmp/array-api.md, ready to be pasted into a github
issue.
"""

class Table:

    HEADER = 'API function'
    has_np = []
    is_fully_tested = []

    @classmethod
    def as_mark(cls, value):
        return ':heavy_check_mark:' if value else ':x:'

    @classmethod
    def print(cls, file=None):
        BASE_URL = 'https://data-apis.org/array-api/latest/API_specification'
        link = f'{BASE_URL}/{cls.__name__}'
        print(f'### [{cls.title}]({link})', file=file)
        print(f'| {cls.HEADER} | Available as `np.*` | Fully tested |', file=file)
        print(f'| ------------ | ------------------- | ------------ |', file=file)
        for name in cls.names:
            has_np = cls.as_mark(name in cls.has_np)
            is_fully_tested = cls.as_mark(name in cls.is_fully_tested)
            print(f'| ``{name}`` | {has_np} | {is_fully_tested} |', file=file)
        print(file=file)


class array_object(Table):
    from function_stubs.array_object import __all__ as names
    title = 'array_object'
    HEADER = 'Method'

class constants(Table):
    from function_stubs.constants import __all__ as names
    title = 'constants'

class creation_functions(Table):
    from function_stubs.creation_functions import __all__ as names
    title = 'Creation functions'

class data_type_functions(Table):
    from function_stubs.data_type_functions import __all__ as names
    title = 'data_type_functions'

class elementwise_functions(Table):
    from function_stubs.elementwise_functions import __all__ as names
    title = 'elementwise_functions'

class linalg(Table):
    from function_stubs.linalg import __all__ as names
    title = 'linalg'

class linear_algebra_functions(Table):
    from function_stubs.linear_algebra_functions import __all__ as names
    title = 'linear_algebra_functions'

class manipulation_functions(Table):
    from function_stubs.manipulation_functions import __all__ as names
    title = 'manipulation_functions'

class searching_functions(Table):
    from function_stubs.searching_functions import __all__ as names
    title = 'searching_functions'

class set_functions(Table):
    from function_stubs.set_functions import __all__ as names
    title = 'set_functions'

class sorting_functions(Table):
    from function_stubs.sorting_functions import __all__ as names
    title = 'sorting_functions'

class statistical_functions(Table):
    from function_stubs.statistical_functions import __all__ as names
    title = 'statistical_functions'

class utility_functions(Table):
    from function_stubs.utility_functions import __all__ as names
    title = 'utility_functions'

if __name__ == '__main__':
    original_print = print
    with open('/tmp/array-api.md', 'w') as f:
        #constants().print(f)
        creation_functions().print(f)
        ## data_type_functions().print(f)
        ## elementwise_functions().print(f)
        ## linalg().print(f)
        ## linear_algebra_functions().print(f)
        ## manipulation_functions().print(f)
        ## searching_functions().print(f)
        ## set_functions().print(f)
        ## sorting_functions().print(f)
        ## statistical_functions().print(f)
        ## utility_functions().print(f)
        ## array_object().print(f)
