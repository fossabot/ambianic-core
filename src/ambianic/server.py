"""Main Ambianic server module."""
import time
import logging
import os
import pathlib
import yaml
from ambianic.webapp.flaskr import FlaskServer
from ambianic.pipeline.interpreter import PipelineServer
from ambianic.util import ServiceExit, stacktrace

log = logging.getLogger(__name__)


AI_MODELS_DIR = "ai_models"
CONFIG_FILE = "config.yaml"
SECRETS_FILE = "secrets.yaml"
DEFAULT_LOG_LEVEL = logging.INFO
MANAGED_SERVICE_HEARTBEAT_THRESHOLD = 10
MAIN_HEARTBEAT_LOG_INTERVAL = 5
ROOT_SERVERS = {
    'pipelines': PipelineServer,
    'web': FlaskServer,
}


def _configure_logging(config=None):
    default_log_level = DEFAULT_LOG_LEVEL
    if config is None:
        config = {}
    log_level = config.get("level", None)
    numeric_level = default_log_level
    if log_level:
        try:
            numeric_level = getattr(logging, log_level.upper(),
                                    DEFAULT_LOG_LEVEL)
        except AttributeError as e:
            log.warning("Invalid log level: %s . Error: %s", log_level, e)
            log.warning('Defaulting log level to %s', default_log_level)
    if numeric_level <= logging.INFO:
        format_cfg = '%(asctime)s %(levelname)-4s ' \
            '%(pathname)s.%(funcName)s(%(lineno)d): %(message)s'
        datefmt_cfg = '%Y-%m-%d %H:%M:%S'
    else:
        format_cfg = None
        datefmt_cfg = None
    log_filename = config.get('file', None)
    if log_filename:
        log_directory = os.path.dirname(log_filename)
        with pathlib.Path(log_directory) as log_dir:
            log_dir.mkdir(parents=True, exist_ok=True)
        print("Log messages directed to {}".format(log_filename))
    root_logger = logging.getLogger()
    # remove any outside handlers
    while root_logger.hasHandlers():
        root_logger.removeHandler(root_logger.handlers[0])
    logging.basicConfig(
        format=format_cfg,
        level=numeric_level,
        datefmt=datefmt_cfg,
        filename=log_filename)
    effective_level = log.getEffectiveLevel()
    assert numeric_level == effective_level
    log.info('Logging configured with level %s',
             logging.getLevelName(effective_level))
    if effective_level <= logging.DEBUG:
        log.debug('Configuration dump:')
        log.debug(yaml.dump(config))


def _configure(env_work_dir=None):
    """Load configuration settings.

    :returns config dict if configuration was loaded without issues.
            None or a specific exception otherwise.
    """
    assert env_work_dir, 'Working directory required.'
    assert os.path.exists(env_work_dir), \
        'working directory invalid: {}'.format(env_work_dir)
    print("Configuring server...")
    secrets_file = os.path.join(env_work_dir, SECRETS_FILE)
    config_file = os.path.join(env_work_dir, CONFIG_FILE)
    try:
        if os.path.isfile(secrets_file):
            with open(secrets_file) as sf:
                secrets_config = sf.read()
        else:
            secrets_config = ""
            log.warning('Secrets file not found. '
                        'Proceeding without it: %s',
                        secrets_file)
        with open(config_file) as cf:
            base_config = cf.read()
            all_config = secrets_config + "\n" + base_config
        config = yaml.safe_load(all_config)
        # configure logging
        logging_config = None
        if config:
            logging_config = config.get('logging', None)
        _configure_logging(logging_config)
        return config
    except Exception as e:
        log.error("Failed to load configuration: %s", str(e))
        stacktrace()
        return None


class AmbianicServer:
    """Ambianic main server."""

    def __init__(self, work_dir=None):
        """Inititalize server from working directory files.

        Parameters
        ----------
        work_dir : string
            The working directory where config and data reside.

        """
        assert work_dir
        self._env_work_dir = work_dir
        # array of managed specialized servers
        self._servers = {}
        self._service_exit_requested = False
        self._latest_heartbeat = time.monotonic()

    def _stop_servers(self, servers):
        log.debug('Stopping servers...')
        for s in servers.values():
            s.stop()

    def _healthcheck(self, servers):
        """Check the health of managed servers."""
        for s in servers.values():
            latest_heartbeat, status = s.healthcheck()
            now = time.monotonic()
            lapse = now - latest_heartbeat
            log.info('lapse for %s is %f', s.__class__.__name__, lapse)
            if lapse > MANAGED_SERVICE_HEARTBEAT_THRESHOLD:
                log.warning('Server "%s" is not responsive. '
                            'Latest heart beat was %f seconds ago. '
                            'Will send heal signal.',
                            s.__class__.__name__, lapse)
                s.heal()

    def _log_heartbeat(self):
        log.info("Main thread alive.")

    def _heartbeat(self):
        new_time = time.monotonic()
        # print a heartbeat message every so many seconds
        if new_time - self._latest_heartbeat > MAIN_HEARTBEAT_LOG_INTERVAL:
            self._log_heartbeat()
            # this is where hooks to external
            # monitoring services will come in
        self._latest_heartbeat = new_time
        if self._service_exit_requested:
            raise ServiceExit

    def start(self):
        """Programmatic start of the main service."""
        print("Starting server...")
        config = _configure(self._env_work_dir)
        if not config:
            log.info('No startup configuration provided. '
                     'Proceeding with defaults.')
        log.info('Starting Ambianic server...')
        # Register the signal handlers
        servers = {}
        # Start the job threads
        try:
            for s_name, s_class in ROOT_SERVERS.items():
                srv = s_class(config=config)
                srv.start()
                servers[s_name] = srv

            self._latest_heartbeat = time.monotonic()

            self._servers = servers
            # Keep the main thread running, otherwise signals are ignored.
            while True:
                time.sleep(0.5)
                self._healthcheck(servers)
                self._heartbeat()

        except ServiceExit:
            log.info('Service exit requested.')
            log.debug('Cleaning up before exit...')
            self._stop_servers(servers)

        log.info('Exiting Ambianic server.')
        return True

    def stop(self):
        """Programmatic stop of the main service."""
        print("Stopping server...")
        log.info("Stopping server...")
        self._service_exit_requested = True
