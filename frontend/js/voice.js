/**
 * Voice interface (Part 5).
 *
 * State machine: IDLE → LISTENING → PROCESSING → SPEAKING → IDLE (or ERROR).
 * Pipeline: MediaRecorder → POST /voice/transcribe → POST /chat (same agent
 * system as text) → POST /voice/speak → Audio playback.
 *
 * Keys never touch this file: audio goes to our backend only.
 */

const VoiceState = Object.freeze({
  IDLE: "IDLE",
  LISTENING: "LISTENING",
  PROCESSING: "PROCESSING",
  SPEAKING: "SPEAKING",
  ERROR: "ERROR",
});

const STATE_LABELS = {
  IDLE: "Tap the microphone and speak",
  LISTENING: "Listening… tap again to stop",
  PROCESSING: "Thinking…",
  SPEAKING: "Speaking…",
  ERROR: "Something went wrong",
};

const micButton = document.getElementById("mic-button");
const micIcon = document.getElementById("mic-icon");
const stateLabel = document.getElementById("voice-state-label");
const voiceError = document.getElementById("voice-error");
const voiceRetry = document.getElementById("voice-retry");
const partialRow = document.getElementById("partial-row");
const partialTranscript = document.getElementById("partial-transcript");
const finalRow = document.getElementById("final-row");
const finalTranscript = document.getElementById("final-transcript");
const responseRow = document.getElementById("response-row");
const responseTranscript = document.getElementById("response-transcript");
const activitySourceEl = document.getElementById("activity-source");

let voiceState = VoiceState.IDLE;
let mediaRecorder = null;
let audioChunks = [];
let audioStream = null;
let playback = null; // current Audio element

// Live/partial transcript via Web Speech API when the browser supports it
// (best-effort enhancement; Deepgram transcript is always authoritative).
const SpeechRecognitionImpl =
  window.SpeechRecognition || window.webkitSpeechRecognition || null;
let recognizer = null;

function setVoiceState(next) {
  voiceState = next;
  micButton.className = `mic-button mic-button--${next.toLowerCase()}`;
  stateLabel.textContent = STATE_LABELS[next];
  micIcon.textContent =
    next === VoiceState.ERROR ? "⚠️" : next === VoiceState.SPEAKING ? "🔊" : "🎙️";

  micButton.disabled = next === VoiceState.PROCESSING;
  voiceRetry.hidden = next !== VoiceState.ERROR;
  if (next !== VoiceState.ERROR) {
    voiceError.hidden = true;
  }
}

function showVoiceError(message) {
  voiceError.textContent = message;
  voiceError.hidden = false;
  setVoiceState(VoiceState.ERROR);
}

// ---------- Recording ----------

async function startListening() {
  if (!navigator.mediaDevices || !window.MediaRecorder) {
    showVoiceError("This browser does not support audio recording.");
    return;
  }

  try {
    // Prompts the browser permission prompt on first use.
    audioStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    showVoiceError(
      err.name === "NotAllowedError"
        ? "Microphone permission denied — allow mic access in your browser settings."
        : "Could not access the microphone."
    );
    return;
  }

  audioChunks = [];
  try {
    mediaRecorder = new MediaRecorder(audioStream);
  } catch (err) {
    stopStream();
    showVoiceError("Audio recording is not supported in this browser.");
    return;
  }

  mediaRecorder.ondataavailable = (event) => {
    if (event.data.size > 0) audioChunks.push(event.data);
  };
  mediaRecorder.onstop = () => handleRecordedAudio(new Blob(audioChunks, { type: mediaRecorder.mimeType }));

  mediaRecorder.start();
  startPartialTranscript();
  setVoiceState(VoiceState.LISTENING);
}

function stopListening() {
  stopPartialTranscript();
  if (mediaRecorder && mediaRecorder.state !== "inactive") mediaRecorder.stop();
  stopStream();
}

function stopStream() {
  if (audioStream) {
    audioStream.getTracks().forEach((track) => track.stop());
    audioStream = null;
  }
}

// ---------- Optional live transcript (browser Web Speech API) ----------

function startPartialTranscript() {
  partialRow.hidden = false;
  partialTranscript.textContent = "";
  if (!SpeechRecognitionImpl) return;

  try {
    recognizer = new SpeechRecognitionImpl();
    recognizer.lang = "en-US";
    recognizer.interimResults = true;
    recognizer.continuous = false;
    recognizer.onresult = (event) => {
      let text = "";
      for (const result of event.results) text += result[0].transcript;
      partialTranscript.textContent = text;
    };
    recognizer.onerror = () => {}; // non-fatal: Deepgram transcript is authoritative
    recognizer.onend = () => { recognizer = null; };
    recognizer.start();
  } catch {
    recognizer = null; // enhancement unavailable — continue silently
  }
}

function stopPartialTranscript() {
  if (recognizer) {
    try { recognizer.stop(); } catch { /* already stopping */ }
    recognizer = null;
  }
}

// ---------- Pipeline: transcribe → chat → speak ----------

async function handleRecordedAudio(blob) {
  if (!blob || blob.size === 0) {
    showVoiceError("No audio captured — try speaking a bit longer.");
    return;
  }

  setVoiceState(VoiceState.PROCESSING);

  let transcript;
  try {
    const result = await transcribeAudio(blob);
    transcript = result.text;
  } catch (err) {
    showVoiceError(err.message || "Transcription failed.");
    return;
  }

  finalRow.hidden = false;
  finalTranscript.textContent = transcript;
  partialRow.hidden = true;

  // Same agent pipeline as typed text — no separate voice chatbot.
  const reply = await submitChat(transcript);
  if (!reply) {
    showVoiceError("The agent could not process that request.");
    return;
  }

  responseRow.hidden = false;
  responseTranscript.textContent = reply.message;

  // TTS: on failure, keep the text answer rather than losing the turn.
  try {
    setVoiceState(VoiceState.SPEAKING);
    const audioBlob = await synthesizeSpeech(reply.message);
    await playAudio(audioBlob);
  } catch (err) {
    console.warn("TTS unavailable:", err.message);
    activitySourceEl && (activitySourceEl.textContent = `Speech playback failed: ${err.message}`);
    setVoiceState(VoiceState.IDLE);
    return;
  }
  setVoiceState(VoiceState.IDLE);
}

function playAudio(blob) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(blob);
    playback = new Audio(url);
    playback.onended = () => { URL.revokeObjectURL(url); resolve(); };
    playback.onerror = () => { URL.revokeObjectURL(url); reject(new Error("audio playback failed")); };
    playback.play().catch(() => { URL.revokeObjectURL(url); reject(new Error("browser blocked playback")); });
  });
}

function stopPlayback() {
  if (playback) {
    playback.pause();
    playback = null;
  }
}

// ---------- Interaction ----------

micButton.addEventListener("click", () => {
  if (voiceState === VoiceState.IDLE || voiceState === VoiceState.ERROR) {
    setVoiceState(VoiceState.LISTENING);
    startListening();
  } else if (voiceState === VoiceState.LISTENING) {
    stopListening(); // onstop → handleRecordedAudio
  } else if (voiceState === VoiceState.SPEAKING) {
    stopPlayback();
    setVoiceState(VoiceState.IDLE);
  }
});

voiceRetry.addEventListener("click", () => setVoiceState(VoiceState.IDLE));

// Hold Space as a keyboard alternative (accessibility).
document.addEventListener("keydown", (event) => {
  if (event.code === "Space" && !event.repeat && isVoiceViewActive() &&
      (voiceState === VoiceState.IDLE || voiceState === VoiceState.ERROR) &&
      document.activeElement !== chatInput) {
    event.preventDefault();
    micButton.click();
  }
});
document.addEventListener("keyup", (event) => {
  if (event.code === "Space" && voiceState === VoiceState.LISTENING && isVoiceViewActive()) {
    event.preventDefault();
    stopListening();
  }
});

function isVoiceViewActive() {
  const view = document.getElementById("view-voice-assistant");
  return view && !view.hidden;
}

setVoiceState(VoiceState.IDLE);
