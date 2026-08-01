// Video player behaviour.
//
// Loaded after the inline bootstrap in player.html, which supplies mediaId,
// episodeParam and knownDurationSeconds.

const hlsUrl = withEp(`/api/video/${mediaId}/hls/master.m3u8`);
const directStreamUrl = withEp(`/api/video/${mediaId}/direct-stream/master.m3u8`);
const streamUrl = withEp(`/api/video/${mediaId}/stream`);
let lastSavedPosition = 0;
let savePositionTimeout = null;
let pendingResumeTime = 0;
let hlsInstance = null;
let activeStrategy = 'hls';
let mediaErrorRecoveries = 0;
let controlsHideTimeout = null;
let isControlsPinned = false;
let pingInterval = null;

function getCaptionTracks() {
    return Array.from(video.textTracks || []);
}

function normalizeLanguageLabel(track, index) {
    const rawLabel = (track.label || '').trim();
    if (rawLabel && !/^subtitles?$/i.test(rawLabel)) {
        return rawLabel;
    }

    const lang = (track.language || '').trim().toLowerCase();
    if (lang) {
        const canonical = lang.replace('_', '-');
        try {
            const displayNames = new Intl.DisplayNames([navigator.language || 'en'], { type: 'language' });
            const resolved = displayNames.of(canonical);
            if (resolved) {
                return resolved;
            }
        } catch (_err) {
            // Fallback map below.
        }

        const fallbackNames = {
            en: 'English',
            eng: 'English',
            es: 'Spanish',
            spa: 'Spanish',
            fr: 'French',
            fra: 'French',
            de: 'German',
            ger: 'German',
            it: 'Italian',
            ita: 'Italian',
            pt: 'Portuguese',
            por: 'Portuguese',
            ja: 'Japanese',
            jpn: 'Japanese',
            ko: 'Korean',
            kor: 'Korean',
            zh: 'Chinese',
            zho: 'Chinese',
        };
        if (fallbackNames[lang]) {
            return fallbackNames[lang];
        }
    }

    return `Track ${index + 1}`;
}

const ICONS = {
    play: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5v14l11-7z"/></svg>',
    pause: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 5h4v14H7zM13 5h4v14h-4z"/></svg>',
    volume: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 9v6h4l5 4V5L7 9H3zm12.5 3a3.5 3.5 0 0 0-2.5-3.35v6.7A3.5 3.5 0 0 0 15.5 12z"/></svg>',
    mute: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 9v6h4l5 4V5L7 9H3zm13.59 3 2.7-2.7-1.42-1.42-2.7 2.7-2.7-2.7-1.42 1.42 2.7 2.7-2.7 2.7 1.42 1.42 2.7-2.7 2.7 2.7 1.42-1.42-2.7-2.7z"/></svg>',
    ccOn: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2zm3.8 7a2.2 2.2 0 0 0 2.2 2.2 2.1 2.1 0 0 0 1.9-1.1l-1.2-.7a.8.8 0 0 1-.7.4.9.9 0 0 1 0-1.8.8.8 0 0 1 .7.4l1.2-.7a2.1 2.1 0 0 0-1.9-1.1A2.2 2.2 0 0 0 7.8 12zm5.8 0a2.2 2.2 0 0 0 2.2 2.2 2.1 2.1 0 0 0 1.9-1.1l-1.2-.7a.8.8 0 0 1-.7.4.9.9 0 0 1 0-1.8.8.8 0 0 1 .7.4l1.2-.7a2.1 2.1 0 0 0-1.9-1.1 2.2 2.2 0 0 0-2.2 2.2z"/></svg>',
    ccOff: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2zm3.8 7a2.2 2.2 0 0 0 2.2 2.2 2.1 2.1 0 0 0 1.9-1.1l-1.2-.7a.8.8 0 0 1-.7.4.9.9 0 0 1 0-1.8.8.8 0 0 1 .7.4l1.2-.7a2.1 2.1 0 0 0-1.9-1.1A2.2 2.2 0 0 0 7.8 12zm5.8 0a2.2 2.2 0 0 0 2.2 2.2 2.1 2.1 0 0 0 1.9-1.1l-1.2-.7a.8.8 0 0 1-.7.4.9.9 0 0 1 0-1.8.8.8 0 0 1 .7.4l1.2-.7a2.1 2.1 0 0 0-1.9-1.1 2.2 2.2 0 0 0-2.2 2.2z"/><path d="M4.2 4.2l15.6 15.6-1.4 1.4L2.8 5.6z"/></svg>',
    fullscreen: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 14H5v5h5v-2H7v-3zm0-4h2V7h3V5H5v5zm10 7h-3v2h5v-5h-2v3zm0-12h-3v2h3v3h2V5h-2z"/></svg>',
    fullscreenExit: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 16H5v3h3v-2h2v-2H8v1zm0-8V5H5v3h2v1h3V7H8zm8 9h-2v2h3v-3h-1v1zm0-9h1V5h-3v2h2v2h2V8z"/></svg>',
};

function iconMarkup(iconName) {
    return ICONS[iconName] || '';
}

function showStatus(message) {
    statusMessage.textContent = message;
    statusMessage.style.display = 'block';
}

function hideStatus() {
    statusMessage.style.display = 'none';
}

function showControls() {
    videoWrapper.classList.add('show-controls');
    clearTimeout(controlsHideTimeout);
}

function hideControlsSoon(delayMs = 1200) {
    clearTimeout(controlsHideTimeout);
    if (video.paused || isControlsPinned) {
        return;
    }
    controlsHideTimeout = setTimeout(() => {
        if (!video.paused && !isControlsPinned) {
            videoWrapper.classList.remove('show-controls');
        }
    }, delayMs);
}

function formatTime(totalSeconds) {
    const seconds = Math.max(0, Math.floor(totalSeconds || 0));
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = seconds % 60;
    if (hours > 0) {
        return `${hours}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
    }
    return `${minutes}:${String(secs).padStart(2, '0')}`;
}

function getDisplayDuration() {
    if (Number.isFinite(knownDurationSeconds) && knownDurationSeconds > 0) {
        return knownDurationSeconds;
    }
    if (Number.isFinite(video.duration) && video.duration > 0) {
        return video.duration;
    }
    return 0;
}

function updateScrubber() {
    const current = Number.isFinite(video.currentTime) ? Math.max(0, video.currentTime) : 0;
    const total = getDisplayDuration();
    currentTimeLabel.textContent = formatTime(current);
    durationLabel.textContent = formatTime(total);
    if (total > 0) {
        const percent = Math.min(100, (current / total) * 100);
        seekBar.value = String(percent);
        seekBar.style.setProperty('--seek-progress', `${percent}%`);
    } else {
        seekBar.style.setProperty('--seek-progress', '0%');
    }
}

function updatePlayPauseButton() {
    playPauseBtn.innerHTML = iconMarkup(video.paused ? 'play' : 'pause');
}

function updateMuteUi() {
    const muted = video.muted || video.volume === 0;
    muteBtn.innerHTML = iconMarkup(muted ? 'mute' : 'volume');
    const displayVolume = video.muted ? 0 : video.volume;
    volumeBar.value = String(displayVolume);
    volumeBar.style.setProperty('--volume-progress', `${Math.max(0, Math.min(100, displayVolume * 100))}%`);
}

function updateCaptionsUi() {
    const tracks = getCaptionTracks();
    const hasTracks = tracks.length > 0;
    captionsBtn.disabled = !hasTracks;
    captionsBtn.classList.remove('cc-disabled');
    if (!hasTracks) {
        captionsBtn.classList.add('cc-disabled');
        captionsBtn.setAttribute('aria-expanded', 'false');
        captionsMenu.hidden = true;
        return;
    }
    const enabled = tracks.some((track) => track.mode === 'showing');
    if (!enabled) {
        captionsBtn.classList.add('cc-disabled');
    }
    buildCaptionsMenu();
}

function setCaptionTrackByIndex(index) {
    const tracks = getCaptionTracks();
    tracks.forEach((track, i) => {
        track.mode = i === index ? 'showing' : 'disabled';
    });
    updateCaptionsUi();
}

function disableCaptions() {
    const tracks = getCaptionTracks();
    tracks.forEach((track) => {
        track.mode = 'disabled';
    });
    updateCaptionsUi();
}

function buildCaptionsMenu() {
    const tracks = getCaptionTracks();
    if (!tracks.length) {
        captionsMenu.innerHTML = '';
        return;
    }

    const activeIndex = tracks.findIndex((track) => track.mode === 'showing');
    const options = [];

    options.push(`<button type="button" class="captions-option ${activeIndex === -1 ? 'active' : ''}" data-caption="off">Off</button>`);

    for (let i = 0; i < tracks.length; i += 1) {
        const track = tracks[i];
        const label = normalizeLanguageLabel(track, i);
        const safeLabel = label
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;');
        options.push(`<button type="button" class="captions-option ${i === activeIndex ? 'active' : ''}" data-caption="${i}">${safeLabel}</button>`);
    }

    captionsMenu.innerHTML = options.join('');
}

function closeCaptionsMenu() {
    captionsMenu.hidden = true;
    captionsBtn.setAttribute('aria-expanded', 'false');
}

function toggleCaptionsMenu() {
    if (captionsBtn.disabled) {
        return;
    }
    const willOpen = captionsMenu.hidden;
    if (willOpen) {
        buildCaptionsMenu();
    }
    captionsMenu.hidden = !willOpen;
    captionsBtn.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
}

function togglePlayPause() {
    if (video.paused) {
        video.play().catch(() => {
            showStatus('Unable to start playback right now.');
        });
    } else {
        video.pause();
    }
}

function toggleMute() {
    video.muted = !video.muted;
    if (!video.muted && video.volume === 0) {
        video.volume = 0.6;
    }
    updateMuteUi();
}

async function toggleFullscreen() {
    try {
        if (document.fullscreenElement) {
            await document.exitFullscreen();
            return;
        }
        await videoWrapper.requestFullscreen();
    } catch (_err) {
        showStatus('Fullscreen is not available.');
    }
}

function applyResumeTime() {
    if (!pendingResumeTime) {
        return;
    }
    // hls.js exposes the entire timeline as seekable because the playlist lists
    // every segment up front; setting currentTime triggers an on-demand transcode
    // for the target segment.
    const total = getDisplayDuration();
    const safeMax = total > 0 ? Math.max(total - 2, 0) : 0;
    if (safeMax <= 0) {
        return;
    }
    const target = Math.min(pendingResumeTime, safeMax);
    try {
        video.currentTime = target;
        lastSavedPosition = target;
    } catch (err) {
        console.warn('Failed to apply resume time:', err);
    }
    pendingResumeTime = 0;
    updateScrubber();
}

async function loadHlsLibrary() {
    if (window.Hls) return true;
    const candidates = [
        'https://cdn.jsdelivr.net/npm/hls.js@1.5.18/dist/hls.min.js',
        'https://unpkg.com/hls.js@1.5.18/dist/hls.min.js',
        'https://cdnjs.cloudflare.com/ajax/libs/hls.js/1.5.18/hls.min.js',
    ];
    for (const url of candidates) {
        const loaded = await new Promise((resolve) => {
            const script = document.createElement('script');
            script.src = url;
            script.async = true;
            script.onload = () => resolve(true);
            script.onerror = () => resolve(false);
            document.head.appendChild(script);
        });
        if (loaded && window.Hls) return true;
    }
    return false;
}

// Direct play: point the video element at /stream. The browser handles seeking
// via HTTP range requests with no transcoding or waiting.
function startDirectPlay() {
    video.src = streamUrl;
}

function destroyHlsInstance() {
    if (pingInterval) {
        clearInterval(pingInterval);
        pingInterval = null;
    }
    if (hlsInstance) {
        hlsInstance.destroy();
        hlsInstance = null;
    }
}

// Send a keep-alive ping to the server to prevent the FFmpeg job from being killed.
// Mirrors Jellyfin's PingTranscodingJob pattern.
async function pingHls() {
    const position = Math.max(0, Number(video.currentTime) || 0);
    try {
        await fetch(withEp(`/api/video/${mediaId}/hls/ping`), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ position_seconds: Math.floor(position) }),
        });
    } catch (_err) {
        // Ping failures are non-fatal; the server will kill the job after its timeout.
    }
}

function shouldUseNativeHls() {
    const ua = navigator.userAgent || '';
    const vendor = navigator.vendor || '';
    return /Safari/i.test(ua)
        && /Apple/i.test(vendor)
        && !/Chrome|Chromium|CriOS|Edg|OPR|Firefox|FxiOS/i.test(ua);
}

async function startHlsPlay(sourceUrl) {
    destroyHlsInstance();
    // Prefer hls.js on Chromium/Edge/Firefox even if the browser claims native HLS support.
    // Native handling on those engines can freeze or expose a non-interactive timeline.
    if (shouldUseNativeHls() && video.canPlayType('application/vnd.apple.mpegurl')) {
        video.src = sourceUrl;
        return true;
    }
    const loaded = await loadHlsLibrary();
    if (loaded && window.Hls && window.Hls.isSupported()) {
        hlsInstance = new window.Hls({ enableWorker: true, lowLatencyMode: false });
        hlsInstance.on(window.Hls.Events.ERROR, function (_event, data) {
            if (!data || !data.fatal) {
                return;
            }

            // Recoverable hls.js failure modes.
            if (data.type === window.Hls.ErrorTypes.NETWORK_ERROR) {
                showStatus('Reconnecting stream...');
                try {
                    hlsInstance.startLoad();
                } catch (_err) {
                    showStatus('Playback network error. Please refresh and try again.');
                }
                return;
            }

            if (data.type === window.Hls.ErrorTypes.MEDIA_ERROR) {
                if (mediaErrorRecoveries < 2) {
                    mediaErrorRecoveries += 1;
                    showStatus('Recovering playback...');
                    try {
                        hlsInstance.recoverMediaError();
                        return;
                    } catch (_err) {
                        // Fall through to hard failure below.
                    }
                }
            }

            // Unrecoverable fatal error.
            destroyHlsInstance();
            showStatus('Playback error. Please refresh and try again.');
        });
        hlsInstance.loadSource(sourceUrl);
        hlsInstance.attachMedia(video);
        return true;
    }
    showStatus('This browser does not support HLS playback. Try Edge, Chrome, Firefox, or Safari.');
    return false;
}

async function loadPlaybackPosition(applyNow = true) {
    try {
        const resp = await fetch(`/api/video/${mediaId}/playback`);
        if (!resp.ok) return 0;
        const data = await resp.json();
        if (data.ok && data.position_seconds > 0) {
            pendingResumeTime = data.position_seconds;
            if (applyNow) {
                applyResumeTime();
            }
            return pendingResumeTime;
        }
        return 0;
    } catch (err) {
        console.error('Failed to load playback position:', err);
        return 0;
    }
}

function savePlaybackPosition() {
    clearTimeout(savePositionTimeout);
    savePositionTimeout = setTimeout(async () => {
        const position = Math.floor(Math.max(0, Number(video.currentTime) || 0));
        if (Math.abs(position - lastSavedPosition) < 2) return;
        lastSavedPosition = position;
        try {
            await fetch(`/api/video/${mediaId}/playback`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    position_seconds: position,
                    duration_seconds: video.duration,
                }),
            });
        } catch (err) {
            console.error('Failed to save playback position:', err);
        }
    }, 500);
}

async function seekFromBar() {
    const total = getDisplayDuration();
    if (!total) {
        return;
    }
    const requestedTime = (Number(seekBar.value) / 100) * total;
    // For HLS the playlist lists every segment, so the entire timeline is seekable;
    // hls.js will request the exact segment and the server lazily transcodes it.
    const target = Math.max(0, Math.min(requestedTime, Math.max(total - 1, 0)));
    try {
        video.currentTime = target;
    } catch (_err) {
        // Ignore transient seek failures.
    }
    updateScrubber();
}

function handleKeyboardShortcuts(event) {
    const activeElement = document.activeElement;
    const isTypingField = activeElement
        && (activeElement.tagName === 'INPUT' || activeElement.tagName === 'TEXTAREA' || activeElement.isContentEditable);

    if (event.code === 'Space' && !isTypingField) {
        event.preventDefault();
        togglePlayPause();
        showControls();
        hideControlsSoon();
        return;
    }

    if (isTypingField) {
        return;
    }

    if (event.key === 'm' || event.key === 'M') {
        event.preventDefault();
        toggleMute();
        showControls();
        hideControlsSoon();
        return;
    }

    if (event.key === 'f' || event.key === 'F') {
        event.preventDefault();
        toggleFullscreen();
        showControls();
        hideControlsSoon();
        return;
    }

    if (event.key === 'c' || event.key === 'C') {
        event.preventDefault();
        toggleCaptionsMenu();
        showControls();
        hideControlsSoon();
    }
}

seekBar.addEventListener('input', function () { seekFromBar().catch(() => {}); });
seekBar.addEventListener('change', function () { seekFromBar().catch(() => {}); });

playPauseBtn.addEventListener('click', togglePlayPause);
muteBtn.addEventListener('click', toggleMute);
captionsBtn.addEventListener('click', function (event) {
    event.stopPropagation();
    toggleCaptionsMenu();
});

captionsMenu.addEventListener('click', function (event) {
    const target = event.target.closest('.captions-option');
    if (!target) {
        return;
    }
    const pick = target.getAttribute('data-caption');
    if (pick === 'off') {
        disableCaptions();
    } else {
        const index = Number.parseInt(pick, 10);
        if (Number.isFinite(index) && index >= 0) {
            setCaptionTrackByIndex(index);
        }
    }
    closeCaptionsMenu();
});

document.addEventListener('click', function (event) {
    if (!captionsMenu.hidden && !event.target.closest('.captions-wrap')) {
        closeCaptionsMenu();
    }
});
fullscreenBtn.addEventListener('click', toggleFullscreen);
volumeBar.addEventListener('input', function () {
    const volume = Number(volumeBar.value);
    if (!Number.isFinite(volume)) {
        return;
    }
    video.volume = Math.max(0, Math.min(1, volume));
    video.muted = video.volume === 0;
    updateMuteUi();
});

videoWrapper.addEventListener('mousemove', function () {
    showControls();
    hideControlsSoon();
});
videoWrapper.addEventListener('mouseenter', showControls);
videoWrapper.addEventListener('mouseleave', function () {
    hideControlsSoon(350);
});

controlOverlay.addEventListener('mouseenter', function () {
    isControlsPinned = true;
    showControls();
});
controlOverlay.addEventListener('mouseleave', function () {
    isControlsPinned = false;
    hideControlsSoon(450);
    closeCaptionsMenu();
});

document.addEventListener('keydown', handleKeyboardShortcuts);

video.addEventListener('timeupdate', function () {
    savePlaybackPosition();
    updateScrubber();
});
video.addEventListener('play', function () {
    updatePlayPauseButton();
    hideControlsSoon();
});
video.addEventListener('pause', function () {
    savePlaybackPosition();
    updatePlayPauseButton();
    showControls();
});
video.addEventListener('loadedmetadata', function () { applyResumeTime(); hideStatus(); updateScrubber(); });
video.addEventListener('durationchange', updateScrubber);
video.addEventListener('volumechange', updateMuteUi);
video.addEventListener('progress', updateScrubber);
video.addEventListener('canplay', function () { hideStatus(); updateScrubber(); });
video.addEventListener('error', function () {
    // For hls.js playback, rely on Hls.Events.ERROR handling above so recoverable
    // media/network issues do not immediately show a terminal failure.
    if ((activeStrategy === 'hls' || activeStrategy === 'direct_stream') && hlsInstance) {
        return;
    }
    showStatus('Unable to play this stream right now.');
});

window.addEventListener('beforeunload', function () {
    destroyHlsInstance();
});

document.addEventListener('fullscreenchange', function () {
    fullscreenBtn.innerHTML = iconMarkup(document.fullscreenElement ? 'fullscreenExit' : 'fullscreen');
});

for (const track of video.textTracks || []) {
    track.addEventListener('cuechange', updateCaptionsUi);
}

// Initialize: ask the server which playback strategy to use.
showControls();
updateScrubber();
updatePlayPauseButton();
updateMuteUi();
updateCaptionsUi();
fullscreenBtn.innerHTML = iconMarkup('fullscreen');

(async () => {
    showStatus('Loading...');

    let strategy = 'hls';
    try {
        const resp = await fetch(withEp(`/api/video/${mediaId}/strategy`), { cache: 'no-store' });
        if (resp.ok) {
            const data = await resp.json();
            if (data.ok) strategy = data.strategy;
        }
    } catch (_err) {
        // Network error — fall back to HLS.
    }

    if (strategy === 'direct_play') {
        activeStrategy = 'direct_play';
        // File is H.264+native-audio: serve it directly, instant seek, no transcoding.
        startDirectPlay();
        await loadPlaybackPosition();
        video.play().catch(() => {});
        video.focus();
        return;
    }

    if (strategy === 'direct_stream') {
        activeStrategy = 'direct_stream';
        showStatus('Starting direct stream...');

        const ok = await startHlsPlay(directStreamUrl);
        if (!ok) return;
        await loadPlaybackPosition();
        video.play().catch(() => {});
        video.focus();
        return;
    }

    // Full transcode path: attach the VOD playlist immediately. hls.js will
    // request segments as needed; the server transcodes them on demand.
    activeStrategy = 'hls';
    const resumeAt = await loadPlaybackPosition(false);
    pendingResumeTime = Math.max(0, Number(resumeAt) || 0);

    const ok = await startHlsPlay(hlsUrl);
    if (!ok) return;
    pingInterval = setInterval(pingHls, 10000);
    video.play().catch(() => {});
    video.focus();
})();
