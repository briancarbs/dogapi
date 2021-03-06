'''

Wraps shell commands and sends the result to Datadog as events. Ex:

dogwrap -n test-job -k $API_KEY --submit_mode all "ls -lah"

Note that you need to enclose your command in quotes to prevent python
from thinking the command line arguments belong to the python command
instead of the wrapped command.

You can also have the script only send events if they fail:

dogwrap -n test-job -k $API_KEY --submit_mode errors "ls -lah"

And you can give the command a timeout too:

dogwrap -n test-job -k $API_KEY --timeout=1 "sleep 3"

'''
import sys
import subprocess
import time
from StringIO import StringIO
from optparse import OptionParser

from dogapi import dog_http_api as dog
from dogapi.common import get_ec2_instance_id

class Timeout(Exception): pass

def poll_proc(proc, sleep_interval, timeout):
    start_time = time.time()
    returncode = None
    while returncode is None:
        returncode = proc.poll()
        if time.time() - start_time > timeout:
            raise Timeout()
        else:
            time.sleep(sleep_interval)
    return returncode

def execute(cmd, cmd_timeout, sigterm_timeout, sigkill_timeout,
            proc_poll_interval):
    start_time = time.time()
    returncode = -1
    stdout = ''
    stderr = ''
    try:
        proc = subprocess.Popen(u' '.join(cmd), stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
    except Exception:
        print >> sys.stderr, u"Failed to execute %s" % (repr(cmd))
        raise
    try:
        returncode = poll_proc(proc, proc_poll_interval, cmd_timeout)
        stdout, stderr = proc.communicate()
        duration = time.time() - start_time
    except Timeout:
        duration = time.time() - start_time
        try:
            proc.terminate()
            sigterm_start = time.time()
            try:
                print >> sys.stderr, "Command timed out after %.2fs, killing with SIGTERM" % (time.time() - start_time)
                poll_proc(proc, proc_poll_interval, sigterm_timeout)
                returncode = Timeout
            except Timeout:
                print >> sys.stderr, "SIGTERM timeout failed after %.2fs, killing with SIGKILL" % (time.time() - sigterm_start)
                proc.kill()
                poll_proc(proc, proc_poll_interval, sigkill_timeout)
                returncode = Timeout
        except OSError, e:
            # Ignore OSError 3: no process found.
            if e.errno != 3:
                raise
    return returncode, stdout, stderr, duration

def main():
    parser = OptionParser()
    parser.add_option('-n', '--name', action='store', type='string')
    parser.add_option('-k', '--api_key', action='store', type='string')
    parser.add_option('-m', '--submit_mode', action='store', type='choice', default='errors', choices=['errors', 'all'])
    parser.add_option('-t', '--timeout', action='store', type='int', default=60*60*24)
    parser.add_option('--sigterm_timeout', action='store', type='int', default=60*2)
    parser.add_option('--sigkill_timeout', action='store', type='int', default=60)
    parser.add_option('--proc_poll_interval', action='store', type='float', default=0.5)
    options, args = parser.parse_args()

    dog.api_key = options.api_key

    cmd = []
    for part in args:
        cmd.extend(part.split(' '))
    returncode, stdout, stderr, duration = execute(cmd, options.timeout,
        options.sigterm_timeout, options.sigkill_timeout,
        options.proc_poll_interval)

    host = get_ec2_instance_id()
    if returncode == 0:
        alert_type = 'success'
        event_title = u'[%s] %s succeeded in %.2fs' % (host, options.name,
                                                       duration)
    elif returncode is Timeout:
        alert_type = 'error'
        event_title = u'[%s] %s timed out after %.2fs' % (host, options.name,
                                                          duration)
        returncode = -1
    else:
        alert_type = 'error'
        event_title = u'[%s] %s failed in %.2fs' % (host, options.name,
                                                    duration)
    event_body = [u'%%%\n',
        u'commmand:\n```\n', u' '.join(cmd), u'\n```\n',
        u'exit code: %s\n\n' % returncode,
    ]
    if stdout:
        event_body.extend([u'stdout:\n```\n', stdout, u'\n```\n'])
    if stderr:
        event_body.extend([u'stderr:\n```\n', stderr, u'\n```\n'])
    event_body.append(u'%%%\n')
    event_body = u''.join(event_body)
    event = {
        'alert_type': alert_type,
        'aggregation_key': options.name,
        'host': host,
    }

    print >> sys.stderr, stderr.strip()
    print >> sys.stdout, stdout.strip()

    if options.submit_mode == 'all' or returncode != 0:
        dog.event(event_title, event_body, **event)

    sys.exit(returncode)

if __name__ == '__main__':
    main()
