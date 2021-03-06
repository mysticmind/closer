import logging
logging.basicConfig( level = logging.INFO )
import pytest
import time
import closer.remote
import closer.exceptions
import concurrent.futures
import subprocess
import random

IP = 'localhost'
USER = 'me'
TEST_SSH_PORT = 60321

class Monitor( object ):
    def __init__( self ):
        self.output = []
        self.exitCode = None
        self.deathNotification = False

    def onDeath( self, exitCode ):
        self.exitCode = exitCode
        self.deathNotification = True

    def onOutput( self, line ):
        self.output.append( line )

class TestRealLiveProcesses( object ):
    @pytest.fixture( params = ( 'closer', 'closer3' ) )
    def closerCommand( self, request ):
        return request.param

    @pytest.fixture( scope = 'session' )
    def dockerContainer( self ):
        docker = subprocess.run( [ 'docker', 'run', '-d', '--name', 'cont', '--network', 'host', 'haarcuba/for_closer', str( TEST_SSH_PORT ) ], stdout = subprocess.PIPE, universal_newlines = True, check = True )
        container = docker.stdout.strip()
        subprocess.run( [ 'docker', 'cp', 'source/closer/closer3.py', f'{container}:/usr/local/lib/python3.5/dist-packages/closer/closer3.py' ], check = True )
        yield container
        subprocess.run( [ 'docker', 'rm', '-f', container ] )

    def augment( self, remote, closerCommand ):
        remote.setCloserCommand( closerCommand )
        remote.sshPort = TEST_SSH_PORT
        remote.sshOptions( 'StrictHostKeyChecking=no' )

    def test_sanity( self, dockerContainer, closerCommand ):
        tested = closer.remote.Remote( USER, IP, "bash -c 'exit 77'", shell = True )
        self.augment( tested, closerCommand )
        exitCode = tested.foreground( check = False )
        assert exitCode == 77

        tag = str( random.random() )
        tested = closer.remote.Remote( USER, IP, f"bash -c 'echo -n {tag}-{tag}-{tag}'", shell = True )
        self.augment( tested, closerCommand )
        output = tested.output()
        assert output == f'{tag}-{tag}-{tag}'

        tested = closer.remote.Remote( USER, IP, 'head -1 /etc/hosts' , shell = True )
        self.augment( tested, closerCommand )
        output = tested.output( binary = True )
        assert b'localhost' in output

    @pytest.fixture
    def forceSamePortForAll( self ):
        PORT = 64000
        original = closer.remote.random.randint
        closer.remote.random.randint = lambda x,y: PORT
        yield PORT
        closer.remote.random.randint = original

    def test_issue_3_try_more_than_one_remote_port( self, dockerContainer, forceSamePortForAll ):
        first = closer.remote.Remote( USER, IP, "bash -c 'echo first'; sleep 200", shell = True )
        second = closer.remote.Remote( USER, IP, "bash -c 'echo second'; sleep 200", shell = True )
        self.augment( first, 'closer3' )
        self.augment( second, 'closer3' )

        first.background()
        CAPTURE_THE_PORT = 1
        time.sleep( CAPTURE_THE_PORT )
        second.background()

        SLACK_FOR_FINDING_THE_SECOND_PROCESS_CONTROL_PORT = 2
        time.sleep( SLACK_FOR_FINDING_THE_SECOND_PROCESS_CONTROL_PORT )

        assert self.processAlive( 'first' )
        assert self.processAlive( 'second' )
        first.terminate()
        second.terminate()
        assert not self.processAlive( 'first' )
        assert not self.processAlive( 'second' )

    def test_capture_output_and_also_return_code( self, dockerContainer, closerCommand ):
        tag = str( random.random() )
        tested = closer.remote.Remote( USER, IP, f"bash -c 'echo -n {tag}-{tag}-{tag} ; exit 88'", shell = True )
        self.augment( tested, closerCommand )
        output = tested.output( check = False )
        assert output == f'{tag}-{tag}-{tag}'
        assert tested.process.returncode == 88

    def test_capture_output_and_error_and_exit_code( self, dockerContainer, closerCommand ):
        tag = str( random.random() )
        tested = closer.remote.Remote( USER, IP, f"bash -c 'echo -n {tag}-{tag}-{tag} ; echo -n {tag}_error > /dev/stderr ; exit 11'", shell = True )
        self.augment( tested, closerCommand )
        tested.run( check = False, stdout = subprocess.PIPE, stderr = subprocess.PIPE )
        assert tested.process.stdout == f'{tag}-{tag}-{tag}'
        assert tested.process.stderr == f'{tag}_error'
        assert tested.process.returncode == 11

    def test_capture_output_raises_on_error_by_default( self, dockerContainer, closerCommand ):
        try:
            tested = closer.remote.Remote( USER, IP, "bash -c 'exit 99'", shell = True )
            self.augment( tested, closerCommand )
            tested.output()
        except closer.exceptions.RemoteProcessError as e:
            assert e.causedBy.returncode == 99
        else:
            pytest.fail( 'expected process failure to raise RemoteProcessError, but it did not' )

    def test_remote_subprocess_dies_when_closer_told_to_quit( self, dockerContainer, closerCommand ):
        tag = str( random.random() )
        tested = closer.remote.Remote( USER, IP, f"bash -c 'sleep 1000; echo tag={tag}'", shell = True )
        self.augment( tested, closerCommand )
        tested.background( cleanup = False )
        assert self.processAlive( 'closer' )
        assert self.processAlive( f'tag={tag}', slack = 0 )
        tested.terminate()
        assert not self.processAlive( f'tag={tag}', slack = 0 )
        assert not self.processAlive( 'closer' )

    def test_remote_subprocess_killed_after_timeout( self, dockerContainer, closerCommand ):
        tag = str( random.random() )
        tested = closer.remote.Remote( USER, IP, f"bash -c 'sleep 60; echo tag={tag}'", shell = True )
        self.augment( tested, closerCommand )
        start = time.time()
        try:
            ENABLE_IPYTHON_DEBUGGING = open( '/dev/null' )
            tested.run( timeout = 2, stdin = ENABLE_IPYTHON_DEBUGGING )
        except closer.exceptions.RemoteProcessTimeout:
            elapsed = time.time() - start
            assert 2 < elapsed and elapsed < 4
            assert not self.processAlive( f'tag={tag}' )
            assert not self.processAlive( 'closer' )
        else:
            pytest.fail( 'expected process failure to raise RemoteProcessTimeout, but it did not' )

    def test_many_closer_processes_in_parallel( self, dockerContainer, closerCommand ):
        tags = [ str( random.random() ) for _ in range( 10 ) ]
        remotes = {}
        for tag in tags:
            remote = closer.remote.Remote( USER, IP, f"bash -c 'sleep 1000; echo tag={tag}'", shell = True )
            self.augment( remote, closerCommand )
            remote.sshPort = TEST_SSH_PORT
            remote.background( cleanup = False )
            remotes[ tag ] = remote
            assert self.processAlive( f'tag={tag}' )

        for tag in tags:
            remote = remotes[ tag ]
            remote.terminate()
            assert not self.processAlive( f'tag={tag}' )

        assert not self.processAlive( 'closer' )

    def test_closer_process_dies_if_remote_subprocess_dies_does_not_raise_if_terminated_after_death( self, dockerContainer, closerCommand ):
        tag = str( random.random() )
        tested = closer.remote.Remote( USER, IP, f"bash -c 'sleep 3; echo tag={tag}'", shell = True )
        self.augment( tested, closerCommand )
        tested.background( cleanup = False )
        assert self.processAlive( 'closer' )
        assert self.processAlive( f'tag={tag}' )
        LET_PROCESS_DIE_NATURALLY = 2
        time.sleep( LET_PROCESS_DIE_NATURALLY )
        assert not self.processAlive( f'tag={tag}' )
        assert not self.processAlive( 'closer' )
        tested.terminate()

    def test_live_monitoring_of_remote_process( self, dockerContainer, closerCommand ):
        tag = str( random.random() )
        tested = closer.remote.Remote( USER, IP, f"bash -c 'for i in 1 2 3 4 5; do echo {tag}_$i; sleep 1; done'", shell = True )
        self.augment( tested, closerCommand )
        monitor = Monitor()
        assert not monitor.deathNotification
        tested.liveMonitor( onOutput = monitor.onOutput, onProcessEnd = monitor.onDeath, cleanup = True )
        assert self.processAlive( 'closer' )
        assert self.processAlive( tag )
        LET_PROCESS_DIE_NATURALLY = 7
        time.sleep( LET_PROCESS_DIE_NATURALLY )
        assert not self.processAlive( tag )
        assert not self.processAlive( 'closer' )
        assert monitor.output == [ f'{tag}_{i}' for i in ( 1, 2, 3, 4, 5 ) ]
        assert monitor.exitCode == 0
        assert monitor.deathNotification

    def test_live_monitoring_and_deliberate_killing_of_remote_process( self, dockerContainer, closerCommand ):
        tag = str( random.random() )
        tested = closer.remote.Remote( USER, IP, f"bash -c 'for i in 1 2 3 4 5 6 7 8 9 10; do echo {tag}_$i; sleep 1; done'", shell = True )
        self.augment( tested, closerCommand )
        monitor = Monitor()
        tested.liveMonitor( onOutput = monitor.onOutput, onProcessEnd = monitor.onDeath, cleanup = True )
        assert not monitor.deathNotification
        assert self.processAlive( 'closer' )
        assert self.processAlive( tag )
        LET_PROCESS_LIVE_A_LITTLE = 3
        time.sleep( LET_PROCESS_LIVE_A_LITTLE )
        tested.terminate()
        SLACK = 1
        time.sleep( SLACK )
        assert not self.processAlive( tag )
        assert not self.processAlive( 'closer' )
        assert len( monitor.output ) > 0
        for index, line in enumerate( monitor.output ):
            assert line == f'{tag}_{index + 1}'
        assert monitor.exitCode != 0
        assert monitor.deathNotification

    def test_live_monitoring_only_output( self, dockerContainer, closerCommand ):
        tag = str( random.random() )
        tested = closer.remote.Remote( USER, IP, f"bash -c 'for i in 1 2 3 4 5 6 7 8 9 10; do echo {tag}_$i; sleep 1; done'", shell = True )
        self.augment( tested, closerCommand )
        monitor = Monitor()
        tested.liveMonitor( onOutput = monitor.onOutput, cleanup = True )
        assert not monitor.deathNotification
        assert self.processAlive( 'closer' )
        assert self.processAlive( tag )
        LET_PROCESS_LIVE_A_LITTLE = 3
        time.sleep( LET_PROCESS_LIVE_A_LITTLE )
        tested.terminate()
        SLACK = 1
        time.sleep( SLACK )
        assert not self.processAlive( tag )
        assert not self.processAlive( 'closer' )
        assert len( monitor.output ) > 0
        for index, line in enumerate( monitor.output ):
            assert line == f'{tag}_{index + 1}'
        assert monitor.exitCode is None
        assert not monitor.deathNotification

    def processAlive( self, searchString, slack = 1 ):
        time.sleep( slack )
        searchString = str( searchString )
        completedProcess = subprocess.run( f"ssh -p {TEST_SSH_PORT} -o StrictHostKeyChecking=no {USER}@{IP} pgrep -fl '{searchString}'", shell = True )
        return completedProcess.returncode == 0
