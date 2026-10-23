"""Text argument contracts for the public Sonos CLI; no device access."""

from dataclasses import dataclass
from typing import Callable, Tuple


def volume(value):
    number = int(value)
    if not 0 <= number <= 100:
        raise ValueError('must be an integer from 0 to 100')
    return number


def boolean(value):
    values = {'true': True, 'false': False, '1': True, '0': False}
    try:
        return values[value.lower()]
    except KeyError:
        raise ValueError('must be true, false, 1 or 0') from None


def nonempty(value):
    if not value.strip():
        raise ValueError('must not be empty')
    return value


def audio_source(value):
    if value not in ('optical', 'line'):
        raise ValueError('must be optical or line')
    return value


def volume_direction(value):
    if value not in ('up', 'down', 'set', 'mute'):
        raise ValueError('must be up, down, set or mute')
    return value


@dataclass(frozen=True)
class Argument:
    name: str
    convert: Callable = nonempty


@dataclass(frozen=True)
class CommandArguments:
    required: Tuple[Argument, ...] = ()
    optional: Tuple[Argument, ...] = ()

    def parse(self, values):
        arguments = self.required + self.optional
        if len(values) < len(self.required):
            missing = ', '.join(arg.name for arg in self.required[len(values):])
            raise ValueError('missing required arguments: ' + missing)
        if len(values) > len(arguments):
            raise ValueError('too many positional arguments')
        converted = []
        for argument, value in zip(arguments, values):
            try:
                converted.append(argument.convert(value))
            except ValueError as error:
                raise ValueError(f'{argument.name}: {error}') from None
        return converted


# Only text-callable parameters belong here. Device objects and collections
# remain Python API parameters, never implicit speaker-name CLI arguments.
COMMANDS = {
    'brown_noise': CommandArguments(),
    'pink_noise': CommandArguments(),
    'discover_weekly': CommandArguments(),
    'random_album': CommandArguments(),
    'random_radio': CommandArguments(),
    'group_vol_set': CommandArguments((Argument('volume', volume),)),
    'group_vol_up': CommandArguments(optional=(Argument('increment', volume),)),
    'group_vol_down': CommandArguments(optional=(Argument('increment', volume),)),
    'vol_up': CommandArguments(optional=(Argument('increment', volume),)),
    'vol_down': CommandArguments(optional=(Argument('increment', volume),)),
    'back_15': CommandArguments(),
    'back_30': CommandArguments(),
    'fwd_15': CommandArguments(),
    'fwd_30': CommandArguments(),
    'next_track': CommandArguments(),
    'prev_track': CommandArguments(),
    'rear_movie': CommandArguments(optional=(Argument('volume', volume),)),
    'rear_movie_resume': CommandArguments(),
    'ensure_rear_movie': CommandArguments(),
    'rear_normal': CommandArguments(),
    'rear_inverted': CommandArguments(),
    'play_soundbyte': CommandArguments((Argument('name'),), (Argument('speaker'),)),
    'make_stereo_pair': CommandArguments((Argument('left speaker'), Argument('right speaker'))),
    'vol_eql_all': CommandArguments(optional=(Argument('volume', volume),)),
    'adjust_volume': CommandArguments(
        (Argument('speaker'), Argument('direction', volume_direction)),
        (Argument('amount', volume),),
    ),
    'play': CommandArguments(optional=(Argument('speaker'),)),
    'pause': CommandArguments(),
    'stop': CommandArguments(),
    'mute': CommandArguments(),
    'start_noise': CommandArguments((Argument('favorite'),), (Argument('volume', volume),)),
    'play_from_faves': CommandArguments(
        (Argument('favorite'),), (Argument('group all', boolean),),
    ),
    'audio_source': CommandArguments(
        (Argument('speaker'), Argument('source', audio_source)),
        (Argument('volume', volume),),
    ),
    'scrub': CommandArguments(optional=(Argument('seconds', int),)),
    'add_to_main_group': CommandArguments((Argument('speaker'),)),
    'remove_from_group': CommandArguments((Argument('speaker'),)),
    'partymode': CommandArguments(optional=(Argument('volume', volume),)),
    'unjoin_all': CommandArguments(),
    'direct': CommandArguments((Argument('speaker'),)),
    'crossfade_on': CommandArguments(),
    'crossfade_off': CommandArguments(),
    'num_devices': CommandArguments(),
}
