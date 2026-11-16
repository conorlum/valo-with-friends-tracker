"""Fixed query explanations, shared by the answers and the run prompt. No imports."""

NOTES = {'where': "health is a word (healthy, hurt, low) after the player's last recorded hit; null means no hit is "
          'recorded so far, not full health; heals are not recorded; a living player with no u/v has no '
          'usable position at that time',
 'knew': 'team knowledge, assuming everything any teammate learned was called. enemies: those the team had '
         'seen, with the place and time of the last sighting. signals: events that found or roughly placed '
         'an enemy, with class found or roughly_placed, time, reason and only a rough_place when positions '
         'are available: from the nearest teammate, a minimap direction (up, down, left, right: a 90 degree '
         'quarter) and a distance band in metres. It is never a spot or a named area; ruled_out: utility '
         'showed where an enemy was NOT, with time, name and reason; for Haunt and Recon, area is where the '
         "pulse looked and found nobody of that name, when the device's position is available. It never "
         "gives the enemy's position. lost_track: the team lost where an enemy was (no place given), with "
         'only time, name and reason. All three lists are null when this round stores no knowledge events. '
         "destroyed: this side's own devices (trip, camera, alarmbot, turret, sound sensor, haunt, recon) "
         'shot by the enemy by this time before anything set them off, with time, device, owner and the area '
         'the device stood in; the shooter is not recorded and a destroyed device tells nothing about where '
         'an enemy was',
 'distance': 'x_looking_off: degrees between where x faced and the direction to the other player (0 is '
             "straight at them, 180 straight away); null when the facing isn't recorded",
 'control': "a and b are the round's side groups; shares are fractions of the area's cells over the span",
 'timeline': 'a destroyed event is a device (trip, camera, alarmbot, turret, sound sensor, haunt, recon) '
             'shot before anything set it off: time, device, owner and the area it stood in. The shooter is '
             'not recorded'}
