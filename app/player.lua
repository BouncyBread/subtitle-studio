-- Subtitle Studio: buffer-aware playback and live subtitle reloads.
local utils = require 'mp.utils'
local dir = os.getenv('SUBTITLE_JOB_DIR')
local target = tonumber(os.getenv('SUBTITLE_BUFFER')) or 60
local waiting, user_paused, expected_pause = true, false, true
local revision, loaded_revision, last_state = -1, -1, ''
local info, status = nil, nil
local timer
local function read_json(name)
    local file = io.open(dir .. '/' .. name, 'r')
    if not file then return nil end
    local data = file:read('*a'); file:close()
    return utils.parse_json(data)
end
local function pause(value)
    if mp.get_property_bool('pause') ~= value then
        expected_pause = value
        mp.set_property_bool('pause', value)
    end
end
mp.observe_property('pause', 'bool', function(_, value)
    if value == nil then return end
    if expected_pause == value then expected_pause = nil; return end
    expected_pause = nil
    user_paused = value
end)
local function clock(seconds)
    seconds = math.max(0, math.floor(seconds))
    return string.format('%d:%02d', math.floor(seconds / 60), seconds % 60)
end
local function load_subtitles()
    if not info or info.version == loaded_revision then return true end
    if info.cue_count == 0 then loaded_revision = info.version; return true end
    local path = dir .. '/live.srt'
    local id
    for _, track in ipairs(mp.get_property_native('track-list', {})) do
        if track.type == 'sub' and track['external-filename'] == path then id = track.id end
    end
    local result, err
    if id then result, err = mp.command_native({'sub-reload', tostring(id)})
    else result, err = mp.command_native({'sub-add', path, 'select', 'Live local subtitles', info.language or ''}) end
    if err then mp.msg.error('Subtitle load: ' .. err); return false end
    loaded_revision = info.version
    return true
end
local function tick()
    if not dir or not mp.get_property_native('path') then return end
    info = read_json('stream.json') or info
    status = read_json('progress.json') or {stage='queued', message='Waiting for transcription…'}
    local loaded = load_subtitles()
    local position = mp.get_property_number('time-pos', 0)
    local coverage = info and info.covered_until or 0
    local speed = mp.get_property_number('speed', 1)
    local ahead = math.max(0, coverage - position)
    local complete = info and info.complete
    local failed = status.stage == 'error' or status.stage == 'cancelled'
    -- Stop before crossing the finalized frontier. A seek beyond it waits too.
    if not complete and ahead <= 2 * speed then waiting = true end
    if loaded and (complete or ahead >= target * speed) and not failed then waiting = false end
    if failed or not loaded then waiting = true end
    pause(waiting or user_paused)
    local message
    if failed then
        message = 'Subtitles stopped. Return to Subtitle Studio.\n' .. (status.message or '')
    elseif not loaded then
        message = 'Waiting for the subtitle track to load…'
    elseif waiting then
        message = 'Building subtitle buffer… ' .. clock(ahead) .. ' / ' .. clock(target * speed)
        if status.stage ~= 'transcribing' then message = message .. '\n' .. (status.message or 'Preparing…') end
        if user_paused then message = message .. '\nPaused by you — press Space to allow playback when ready.' end
    end
    if message then mp.osd_message(message, 1.2) end
    local state = {buffering=waiting, user_paused=user_paused, position=position,
        covered_until=coverage, buffer_seconds=ahead, complete=complete or false,
        subtitle_version=loaded_revision, failed=failed}
    local encoded = utils.format_json(state)
    if encoded ~= last_state then
        local file = io.open(dir .. '/player-state.json.tmp', 'w')
        if file then file:write(encoded); file:close(); os.rename(dir .. '/player-state.json.tmp', dir .. '/player-state.json') end
        last_state = encoded
    end
end
mp.register_event('file-loaded', function() tick() end)
mp.register_event('seek', function() tick() end)
mp.add_forced_key_binding('SPACE', 'studio-pause', function() user_paused = not user_paused; tick() end)
mp.add_forced_key_binding('p', 'studio-pause-p', function() user_paused = not user_paused; tick() end)
mp.add_key_binding('b', 'studio-buffer-info', function()
    mp.osd_message('Subtitles ready through ' .. clock(info and info.covered_until or 0) .. '\nSpace: pause · F: fullscreen · Q: close player', 4)
end)
timer = mp.add_periodic_timer(0.5, tick)
