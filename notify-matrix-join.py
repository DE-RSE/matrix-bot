#!/usr/bin/env python3
""" Provide email notifications and invites to new matrix space/room members

Help is available with the "--help" option.

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU Affero General Public License as published by the Free
Software Foundation, either version 3 of the License, or (at your option) any
later version.
This program is distributed in the hope that it will be useful, but WITHOUT
ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
FOR A PARTICULAR PURPOSE. See the GNU Affero General Public License for more
details. You should have received a copy of the GNU General Public License
along with this program. If not, see <http://www.gnu.org/licenses/>.
"""

__author__ = "Frank Löffler"
__contact__ = "frank.loeffler@uni-jena.de"
__copyright__ = "Copyright 2024, Frank Löffler; 2024 Friedrich-Schiller-Universität Jena"
__date__ = "2024-08-02"
__email__ = "frank.loeffler@uni-jena.de"
__license__ = "AGPLv3"
__maintainer__ = "frank.loeffler@uni-jena.de"
__status__ = "Production"
__version__ = "0.1.0"

import sys, argparse
import time
from datetime import datetime
import smtplib
from email.message import EmailMessage

import traceback
from pprint import pprint

# this is matrix-nio, **not** nio
import nio
import simplematrixbotlib as botlib

import requests, json

config = {}
def log(*args, **kwargs):
    if 'verbose' in config:
        print(datetime.now().isoformat(), *args, **kwargs)

def parse_commandline():
    parser = argparse.ArgumentParser(
        description="Email notifications and matrix invites for user joins.",
        epilog='Example usage (replace the echo with the respective command for your '
               "password manager):\n"
               'echo matrix_passwd  | ./matrix_notify.py '
               '--matrixhost synapse.mymatrix.nowhere '
               '--matrixuser matrixuser '
               '--matrixpass - '
               "\n",
        add_help=False)
    req = parser.add_argument_group('required arguments')
    opt = parser.add_argument_group('optional arguments')
    opt.add_argument("-h", "--help", action="help", help="show this help message and exit")
    req.add_argument('--matrixhost', dest='matrix_host', required=True,
                     help='hostname of the matrix server (not including https://)')
    req.add_argument('--matrixuser', dest='matrix_user', required=True,
                     help='user name for the matrix server')
    req.add_argument('--matrixpass', dest='matrix_pass', required=True,
                     help='password for the user on matrix, read from stdin if == "-". ')
    req.add_argument('--matrixspace', dest='space', required=True,
                     help='name of the space to watch')
    opt.add_argument('--matrixwatch', dest='watch', nargs='*',
                     help="List of matrix rooms to watch for new members. New members are those that join one of the rooms in this list (or the given space) and have previously not been member by one of these rooms (nor the space).")
    opt.add_argument('--matrixinvite', dest='invite', nargs='*',
                     help="List of matrix rooms to invite to if new users join the space")
    req.add_argument('--smtphost', dest='smtp_host', required=True,
                     help='hostname of the email (SMTP) server')
    opt.add_argument('--smtpport', dest='smtp_port', required=False, type=int, default=587,
                     help='port of the email (SMTP) server; default: 587')
    req.add_argument('--smtpuser', dest='smtp_user', required=True,
                     help='username for the email (SMTP) server')
    req.add_argument('--smtppass', dest='smtp_pass', required=True,
                     help='password for the email username, read from stdin if == "-", (after --matrixpass -)')
    opt.add_argument('--emailsubject', dest='email_subject', required=False,
                     default='New member',
                     help='Subject of the notification email')
    req.add_argument('--emailfrom', dest='email_from', required=True,
                     help='Email address to use as sender')
    req.add_argument('--emailto', dest='email_to', required=True,
                     help='Email address to send notifications to')
    opt.add_argument('--emailreplyto', dest='email_replyto', required=False,
                     help='Email address to set as Reply-To header')
    opt.add_argument('--verbose', action='store_true',
                     help='Be verbose. By default nothing will be printed if everything works '
                          'as planned.')
    config.update(vars(parser.parse_args()))
    if config['matrix_pass'] == '-':
        config['matrix_pass'] = sys.stdin.readline().rstrip('\n')
    if config['smtp_pass'] == '-':
        config['smtp_pass'] = sys.stdin.readline().rstrip('\n')
    return config

# parse the command line options
config = parse_commandline()

creds = botlib.Creds(config["matrix_host"], config["matrix_user"], config["matrix_pass"])
bot = botlib.Bot(creds)

watched_rooms = [config["space"],]
if config['watch']:
    watched_rooms = watched_rooms + config['watch']
to_invite = []
if config['invite']:
    to_invite = to_invite + config['invite']
space_id = None
watched_room_ids = []
to_invite_ids = []

def room_members(room_id):
    s = requests.Session()
    r = s.get(f'{bot.creds.homeserver}/_matrix/client/v3/rooms/{room_id}/members?access_token={bot.creds.access_token}&membership=join')
    if r.status_code != 200:
        return []
    try:
        members = [umap['user_id'] for umap in json.loads(r.text)['chunk']]
    except Exception as e:
        log('room_members', repr(e), e)
        return []
    return members

def populate_watched_room_ids():
    global watched_room_ids, to_invite_ids, space_id
    s = requests.Session()
    r = s.get(f'{bot.creds.homeserver}/_matrix/client/v3/joined_rooms?access_token={bot.creds.access_token}')
    if r.status_code != 200:
        return []
    try:
        room_ids = json.loads(r.text)['joined_rooms']
    except Exception as e:
        log('joined rooms', repr(e), e)
        return []
    watched_room_ids = []
    to_invite_ids = []
    for room_id in room_ids:
        r = s.get(f'{bot.creds.homeserver}/_matrix/client/v3/rooms/{room_id}/state/m.room.name?access_token={bot.creds.access_token}')
        # rooms do not have to have a name
        if r.status_code != 200:
            continue
        room_name = json.loads(r.text)['name']
        log("subscribed to: ", room_name)
        if room_name in watched_rooms:
            log(f'  {len(room_members(room_id))} members')
            watched_room_ids.append(room_id)
        if room_name == config["space"]:
            space_id = room_id
        if room_name in to_invite:
            log('  invitation target')
            to_invite_ids.append(room_id)
    if len(watched_rooms) != len(watched_room_ids):
        print('Cannot find ids of all rooms I am supposed to watch. Maybe the matrix user did not join all of them or some of them do not have names set.')

def user_already_known(user_id, exclude_room=None):
    """See if a given user is already member of any watched rooms, except for the one given"""
    for room_id in watched_room_ids:
        if exclude_room == room_id:
            continue
        if user_id in room_members(room_id):
            return True
    return False

@bot.listener.on_startup
async def room_joined(room_id):
    if len(watched_room_ids) == 0:
        populate_watched_room_ids()
        log(f'This bot is now watching rooms with the ids {watched_room_ids}')

@bot.listener.on_custom_event(nio.RoomMemberEvent)
# This is called for all events while we listen
async def notify(room, event):
    if event.membership == "join" and event.membership != event.prev_membership:
        log(f'noticed a user joining room "{room.display_name}" ({room.room_id})')
        if user_already_known(event.state_key, exclude_room=room.room_id):
            log("   ... but user is already known, not doing anything")
        else:
            room_type = "room"
            # invite into some rooms if people join space (and are not in other watched rooms)
            if room.room_id == space_id:
                room_type = "space"
                s = requests.Session()
                for to_invite_id in to_invite_ids:
                    r = s.post(f'{bot.creds.homeserver}/_matrix/client/v3/rooms/{to_invite_id}/invite?access_token={bot.creds.access_token}', json={'user_id': event.state_key})
            # sent an email for new users
            if room.display_name in watched_rooms:
                try:
                    msg = EmailMessage()
                    log(   f'   ... new member "{event.content["displayname"]}" ({event.state_key}) joined the matrix {room_type} {room.display_name}')
                    msg.set_content(f'New member "{event.content["displayname"]}" ({event.state_key}) joined the matrix {room_type} {room.display_name}')
                    msg['Subject']  = config['email_subject']
                    msg['From']     = config['email_from']
                    msg['To']       = config['email_to']
                    msg['Reply-To'] = config['email_replyto']
                    s = smtplib.SMTP(config['smtp_host'], config['smtp_port'])
                    s.ehlo()
                    s.starttls()
                    s.ehlo()
                    s.login(config['smtp_user'], config['smtp_pass'])
                    s.sendmail(msg['From'], msg['To'], msg.as_string())
                    log("   ... email sent")
                    s.quit()
                # print some info if sending the email fails, but continue listening
                except Exception as e:
                    log(f'Exception "{e}" caught while sending email for room {room} and event {event}')

while True:
    try:
        bot.run()
    except Exception as e:
        log(f'Exception {repr(e)}: {e} caught while running bot: sleeping for 10 s and retrying')
        #log(traceback.format_exc())
    time.sleep(10)
