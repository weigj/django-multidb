import os, copy
from django.conf import settings, LazySettings
from django.core import signals
from django.core.exceptions import ImproperlyConfigured
from django.utils.functional import curry
try:
    import thread
except ImportError:
    import dummy_thread as thread
    
__all__ = ('backend', 'connection', 'DatabaseError', 'IntegrityError', 'get_backend', 'get_connection', 'get_current_connection','ConnectionManagementError', 'using')

if not settings.DATABASE_ENGINE:
    settings.DATABASE_ENGINE = 'dummy'

try:
    # Most of the time, the database backend will be one of the official
    # backends that ships with Django, so look there first.
    _import_path = 'django.db.backends.'
    backend = __import__('%s%s.base' % (_import_path, settings.DATABASE_ENGINE), {}, {}, [''])
except ImportError, e:
    # If the import failed, we might be looking for a database backend
    # distributed external to Django. So we'll try that next.
    try:
        _import_path = ''
        backend = __import__('%s.base' % settings.DATABASE_ENGINE, {}, {}, [''])
    except ImportError, e_user:
        # The database backend wasn't found. Display a helpful error message
        # listing all possible (built-in) database backends.
        backend_dir = os.path.join(__path__[0], 'backends')
        try:
            available_backends = [f for f in os.listdir(backend_dir) if not f.startswith('_') and not f.startswith('.') and not f.endswith('.py') and not f.endswith('.pyc')]
        except EnvironmentError:
            available_backends = []
        available_backends.sort()
        if settings.DATABASE_ENGINE not in available_backends:
            raise ImproperlyConfigured, "%r isn't an available database backend. Available options are: %s\nError was: %s" % \
                  (settings.DATABASE_ENGINE, ", ".join(map(repr, available_backends)), e_user)
        else:
            raise # If there's some other error, this must be an error in Django itself.

# Convenient aliases for backend bits.
_connection = backend.DatabaseWrapper(**settings.DATABASE_OPTIONS)
DatabaseError = backend.DatabaseError
IntegrityError = backend.IntegrityError

_backends = {}
_settings = {}
_connections = {}
state = {}

for name in settings.DATABASES:
    database = settings.DATABASES[name]
    if not database['DATABASE_ENGINE'] in _backends:
        try:
            backend = __import__('django.db.backends.' + database['DATABASE_ENGINE']
                                 + ".base", {}, {}, ['base'])
        except ImportError, e:
            try:
                backend = __import__('%s.base' % database['DATABASE_ENGINE'], {}, {}, [''])
            except ImportError, e_user:
                raise	
        _backends[database['DATABASE_ENGINE']] = backend
    options = LazySettings()
    for key, value in database.iteritems():
        setattr(options, key, value)
    _settings[name] = options
    wrapper = backend.DatabaseWrapper(**options.DATABASE_OPTIONS)
    wrapper.settings = options
    wrapper.name = name
    _connections[name] = wrapper

def get_backend(name):
    return _backends[settings.DATABASES[name]['DATABASE_ENGINE']]

def get_connection(name):
    wrapper = _connections[name]
    return wrapper

class ConnectionManagementError(Exception):
    pass

def enter_connection_management(connection):
    thread_ident = thread.get_ident()
    if thread_ident in state and state[thread_ident]:
        state[thread_ident].append(connection)
    else:
        state[thread_ident] = []
        state[thread_ident].append(connection)

def leave_connection_management():
    thread_ident = thread.get_ident()
    if thread_ident in state and state[thread_ident]:
        del state[thread_ident][-1]
    else:
        raise ConnectionManagementError("This code isn't under connection management")

class using(object):
    def __init__(self, database):
        self.database = database
    def __enter__(self):
        enter_connection_management(get_connection(self.database))
    def __exit__(self, etyp, einst, etb):
        leave_connection_management()

class ConnectionDescriptor(object):
    def __getattribute__(self, key):
        db =  get_current_connection()
        return db.__getattribute__(key)

def get_current_connection():
    thread_ident = thread.get_ident()
    if thread_ident in state and state[thread_ident]:
        return state[thread_ident][-1]
    else:
        return _connection

connection = ConnectionDescriptor()

# Register an event that closes the database connection
# when a Django request is finished.
def close_connection(**kwargs):
    get_current_connection().close() #connection.close()
signals.request_finished.connect(close_connection)

# Register an event that resets connection.queries
# when a Django request is started.
def reset_queries(**kwargs):
    get_current_connection().queries = []
signals.request_started.connect(reset_queries)

# Register an event that rolls back the connection
# when a Django request has an exception.
def _rollback_on_exception(**kwargs):
    from django.db import transaction
    try:
        transaction.rollback_unless_managed()
    except DatabaseError:
        pass
signals.got_request_exception.connect(_rollback_on_exception)
