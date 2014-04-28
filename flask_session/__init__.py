# -*- coding: utf-8 -*-
"""
    flaskext.session
    ~~~~~~~~~~~~~~~~

    Adds server session support to your application.

    :copyright: (c) 2014 by Shipeng Feng.
    :license: BSD, see LICENSE for more details.
"""
import os

from .sessions import NullSessionInterface, RedisSessionInterface, \
     MemcachedSessionInterface, FileSystemSessionInterface, \
     MySQLSessionInterface, MongoDBSessionInterface


class Session(object):
    """This class is used to add Server-side Session to one or more Flask
    applications.

    There are two usage modes.  One is initialize the instance with a very
    specific Flask application::

        app = Flask(__name__)
        Session(app)

    The second possibility is to create the object once and configure the
    application later::
        
        sess = Session()

        def create_app():
            app = Flask(__name__)
            sess.init_app(app)
            return app

    By default Flask-Session will use :class:`NullSessionInterface`, you
    really should configurate your app to use a different SessionInterface.

    .. note::

        You can not use ``Session`` instance directly, what ``Session`` does
        is just change the :attr:`~flask.Flask.session_interface` attribute on
        your Flask applications.
    """
    
    def __init__(self, app=None):
        self.app = app
        if app is not None:
            self.init_app(app)

    def init_app(self, app):
        "This is used to set up session for your app object."
        app.session_interface = self.get_interface(app)

    def get_interface(self, app):
        config = app.config.copy()
        config.setdefault('SESSION_TYPE', 'memcached')
        config.setdefault('SESSION_KEY_PREFIX', 'session:')
        config.setdefault('SESSION_REDIS', None)
        config.setdefault('SESSION_MEMCACHED', None)
        config.setdefault('SESSION_FILE_DIR', os.path.join(os.getcwd(), 
                                                           'flask_session'))
        config.setdefault('SESSION_FILE_THRESHOLD', 500)
        config.setdefault('SESSION_FILE_MODE', 384)

        if config['SESSION_TYPE'] == 'redis':
            session_interface = RedisSessionInterface(config['SESSION_REDIS'],
                                                 config['SESSION_KEY_PREFIX'])
        elif config['SESSION_TYPE'] == 'memcached':
            session_interface = MemcachedSessionInterface(
              config['SESSION_MEMCACHED'], config['SESSION_KEY_PREFIX'])
        elif config['SESSION_TYPE'] == 'filesystem':
            session_interface = FileSystemSessionInterface(
              config['SESSION_FILE_DIR'], config['SESSION_FILE_THRESHOLD'], 
              config['SESSION_FILE_MODE'], config['SESSION_KEY_PREFIX'])
        else:
            session_interface = NullSessionInterface()
        
        return session_interface
