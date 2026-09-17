(() => {
  "use strict";

  const root = document.querySelector(".creation-page");
  if (!root) return;

  const maxSeconds = Number(root.dataset.maxSeconds || 30);

  const recorder = document.getElementById("recorder");
  const recordButton = document.getElementById("record-button");
  const recordLabel = document.getElementById("record-button-label");
  const recorderStatus = document.getElementById("recorder-status");
  const recorderTimer = document.getElementById("recorder-timer");
  const recorderHelp = document.getElementById("recorder-help");
  const generateButton = document.getElementById("generate-button");
  const audioFileInput = document.getElementById("audio-file");
  const fileName = document.getElementById("file-name");
  const formError = document.getElementById("form-error");
  const resultSection = document.getElementById("result-section");
  const resultMessage = document.getElementById("result-message");
  const resultTheme = document.getElementById("result-theme");
  const resultInput = document.getElementById("result-input");
  const voiceDnaBars = document.getElementById("voice-dna-bars");
  const artworkImage = document.getElementById("artwork-image");
  const artworkPlaceholder = document.getElementById("artwork-placeholder");
  const downloadButton = document.getElementById("download-button");
  const saveButton = document.getElementById("save-button");
  const saveStatus = document.getElementById("save-status");

  let audioContext = null;
  let mediaStream = null;
  let sourceNode = null;
  let processorNode = null;
  let silentGain = null;
  let recordedSamples = [];
  let recordedSampleCount = 0;
  let timerId = null;
  let elapsed = 0;
  let source = null;
  let recording = false;
  let latestResult = null;

  function setError(message) {
    formError.textContent = message || "";
    formError.hidden = !message;
  }

  function formatTime(seconds) {
    const total = Math.max(0, Math.floor(seconds));
    const mins = String(Math.floor(total / 60)).padStart(2, "0");
    const secs = String(total % 60).padStart(2, "0");
    return `${mins}:${secs}`;
  }

  function getTheme() {
    return (
      document.querySelector('input[name="theme"]:checked')?.value ||
      "surprise"
    );
  }

  function updateGenerateState() {
    generateButton.disabled = !source?.blob || recording;
  }

  function stopTimer() {
    if (timerId !== null) {
      window.clearInterval(timerId);
      timerId = null;
    }
  }

  function cleanupAudioGraph() {
    if (processorNode) {
      processorNode.onaudioprocess = null;

      try {
        processorNode.disconnect();
      } catch (_) {
        // Ignore cleanup errors.
      }

      processorNode = null;
    }

    if (sourceNode) {
      try {
        sourceNode.disconnect();
      } catch (_) {
        // Ignore cleanup errors.
      }

      sourceNode = null;
    }

    if (silentGain) {
      try {
        silentGain.disconnect();
      } catch (_) {
        // Ignore cleanup errors.
      }

      silentGain = null;
    }

    if (mediaStream) {
      mediaStream.getTracks().forEach((track) => track.stop());
      mediaStream = null;
    }

    if (audioContext) {
      const context = audioContext;
      audioContext = null;
      context.close().catch(() => {});
    }
  }

  function resetRecordingVisuals() {
    stopTimer();
    recorder.classList.remove("is-recording");
    recordLabel.textContent = "Start speaking";
  }

  function mergeBuffers(buffers, length) {
    const merged = new Float32Array(length);
    let offset = 0;

    for (const buffer of buffers) {
      merged.set(buffer, offset);
      offset += buffer.length;
    }

    return merged;
  }

  function encodeWav(samples, sampleRate) {
    const bytesPerSample = 2;
    const dataLength = samples.length * bytesPerSample;
    const buffer = new ArrayBuffer(44 + dataLength);
    const view = new DataView(buffer);

    function writeString(offset, value) {
      for (let i = 0; i < value.length; i += 1) {
        view.setUint8(offset + i, value.charCodeAt(i));
      }
    }

    writeString(0, "RIFF");
    view.setUint32(4, 36 + dataLength, true);
    writeString(8, "WAVE");

    writeString(12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * bytesPerSample, true);
    view.setUint16(32, bytesPerSample, true);
    view.setUint16(34, 16, true);

    writeString(36, "data");
    view.setUint32(40, dataLength, true);

    let offset = 44;

    for (let i = 0; i < samples.length; i += 1) {
      const sample = Math.max(-1, Math.min(1, samples[i]));
      const pcm =
        sample < 0
          ? sample * 0x8000
          : sample * 0x7fff;

      view.setInt16(offset, pcm, true);
      offset += 2;
    }

    return new Blob([buffer], {
      type: "audio/wav",
    });
  }

  function getAudioContextConstructor() {
    return window.AudioContext || window.webkitAudioContext || null;
  }

  async function startRecording() {
    setError("");
    source = null;

    audioFileInput.value = "";
    fileName.textContent = "";

    recordedSamples = [];
    recordedSampleCount = 0;
    elapsed = 0;

    recorderTimer.textContent = formatTime(0);

    updateGenerateState();

    const AudioContextCtor = getAudioContextConstructor();

    if (!navigator.mediaDevices?.getUserMedia || !AudioContextCtor) {
      recorderStatus.textContent = "Microphone unavailable";
      recorderHelp.textContent =
        "Your browser cannot record from a microphone. Use an audio file instead.";

      setError(
        "Microphone recording is not supported by this browser. Please use an audio file instead."
      );

      return;
    }

    try {
      audioContext = new AudioContextCtor();

      mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });

      await audioContext.resume();

      sourceNode = audioContext.createMediaStreamSource(mediaStream);

      processorNode = audioContext.createScriptProcessor(
        4096,
        1,
        1
      );

      silentGain = audioContext.createGain();
      silentGain.gain.value = 0;

      processorNode.onaudioprocess = (event) => {
        if (!recording) return;

        const input = event.inputBuffer.getChannelData(0);
        const copy = new Float32Array(input.length);

        copy.set(input);

        recordedSamples.push(copy);
        recordedSampleCount += copy.length;
      };

      sourceNode.connect(processorNode);
      processorNode.connect(silentGain);
      silentGain.connect(audioContext.destination);

      recording = true;

      recorder.classList.add("is-recording");
      recordLabel.textContent = "Stop speaking";

      recorderStatus.textContent = "Recording";
      recorderHelp.textContent =
        `Speak naturally. Recording stops automatically at ${maxSeconds} seconds.`;

      timerId = window.setInterval(() => {
        elapsed += 1;
        recorderTimer.textContent = formatTime(elapsed);

        if (elapsed >= maxSeconds) {
          stopRecording();
        }
      }, 1000);
    } catch (error) {
      console.warn("Microphone error", error);

      recording = false;
      cleanupAudioGraph();
      resetRecordingVisuals();

      recorderStatus.textContent = "Microphone unavailable";
      recorderHelp.textContent =
        "Allow microphone access or use an audio file instead.";

      setError(
        "reotoi could not access your microphone. Check your browser permission and try again, or use an audio file instead."
      );

      updateGenerateState();
    }
  }

  function stopRecording() {
    if (!recording) return;

    recording = false;
    stopTimer();

    const sampleRate = audioContext?.sampleRate || 44100;
    const samples = mergeBuffers(
      recordedSamples,
      recordedSampleCount
    );

    const duration = samples.length / sampleRate;

    cleanupAudioGraph();
    resetRecordingVisuals();

    if (!samples.length || duration < 0.2) {
      recorderStatus.textContent = "No usable recording";
      recorderHelp.textContent =
        "Please speak for a little longer and try again.";

      setError(
        "The microphone recording was too short. Please record again."
      );

      updateGenerateState();
      return;
    }

    const wavBlob = encodeWav(samples, sampleRate);

    source = {
      type: "microphone",
      blob: wavBlob,
      filename: `reotoi-${Date.now()}.wav`,
    };

    recorderStatus.textContent = "Recording ready";
    recorderHelp.textContent =
      "Your microphone recording is ready to become artwork.";

    updateGenerateState();
  }

  recordButton.addEventListener("click", () => {
    if (recording) {
      stopRecording();
    } else {
      startRecording();
    }
  });

  async function normalizeUploadedAudio(file) {
    if (!file) {
      throw new Error("Please choose an audio file.");
    }

    if (file.size > 20 * 1024 * 1024) {
      throw new Error(
        "Please choose an audio file smaller than 20 MB."
      );
    }

    const AudioContextCtor = getAudioContextConstructor();

    if (!AudioContextCtor) {
      throw new Error(
        "This browser cannot normalize audio files. Please choose a WAV file instead."
      );
    }

    const context = new AudioContextCtor();

    try {
      const arrayBuffer = await file.arrayBuffer();

      const decoded = await context.decodeAudioData(
        arrayBuffer.slice(0)
      );

      if (decoded.duration > maxSeconds + 0.25) {
        throw new Error(
          `Audio must be ${maxSeconds} seconds or less.`
        );
      }

      if (decoded.duration < 0.2) {
        throw new Error(
          "The audio file is too short. Please choose a longer recording."
        );
      }

      const frameCount = decoded.length;
      const channelCount = decoded.numberOfChannels;
      const mono = new Float32Array(frameCount);

      if (channelCount === 1) {
        mono.set(decoded.getChannelData(0));
      } else {
        for (
          let channel = 0;
          channel < channelCount;
          channel += 1
        ) {
          const data = decoded.getChannelData(channel);

          for (let i = 0; i < frameCount; i += 1) {
            mono[i] += data[i] / channelCount;
          }
        }
      }

      return encodeWav(mono, decoded.sampleRate);
    } catch (error) {
      if (
        error instanceof Error &&
        error.message &&
        (
          error.message.includes("Audio must") ||
          error.message.includes("too short") ||
          error.message.includes("cannot normalize")
        )
      ) {
        throw error;
      }

      throw new Error(
        "reotoi could not decode this audio file in your browser. Try a WAV, MP3, OGG, or M4A file that your browser can play."
      );
    } finally {
      await context.close().catch(() => {});
    }
  }

  audioFileInput.addEventListener("change", async () => {
    setError("");

    const file = audioFileInput.files?.[0];

    if (!file) return;

    recording = false;
    cleanupAudioGraph();
    resetRecordingVisuals();

    source = null;
    updateGenerateState();

    fileName.textContent = file.name;

    recorderStatus.textContent = "Preparing audio file";
    recorderHelp.textContent =
      "Normalizing the file for voice analysis…";

    try {
      const wavBlob = await normalizeUploadedAudio(file);

      source = {
        type: "upload",
        blob: wavBlob,
        filename: `reotoi-upload-${Date.now()}.wav`,
        originalFilename: file.name,
      };

      recorderStatus.textContent = "Audio file ready";
      recorderHelp.textContent =
        "The audio file has been normalized and is ready to become artwork.";
    } catch (error) {
      source = null;

      recorderStatus.textContent = "Audio file unavailable";
      recorderHelp.textContent =
        "Choose another file or use the microphone instead.";

      setError(
        error instanceof Error
          ? error.message
          : "reotoi could not read this audio file."
      );
    }

    updateGenerateState();
  });

  function renderVoiceDna(dna) {
    voiceDnaBars.innerHTML = "";

    const labels = [
      "pitch",
      "energy",
      "rhythm",
      "variation",
      "pause",
    ];

    labels.forEach((key) => {
      const value = Number(dna?.[key] ?? 0);
      const clamped = Math.max(
        0,
        Math.min(10, value)
      );

      const row = document.createElement("div");
      row.className = "dna-row";

      const label = document.createElement("span");
      label.className = "dna-row__label";
      label.textContent =
        key.charAt(0).toUpperCase() +
        key.slice(1);

      const track = document.createElement("span");
      track.className = "dna-row__track";
      track.setAttribute("aria-hidden", "true");

      const fill = document.createElement("span");
      fill.className = "dna-row__fill";
      fill.style.width = `${clamped * 10}%`;

      track.appendChild(fill);

      const displayValue = document.createElement("span");
      displayValue.className = "dna-row__value";
      displayValue.textContent =
        `${clamped.toFixed(1)}/10`;

      row.append(
        label,
        track,
        displayValue
      );

      voiceDnaBars.appendChild(row);
    });
  }

  function showResult(data) {
    latestResult = data;

    resultSection.hidden = false;

    resultMessage.textContent =
      data.message ||
      "Your recording has been converted into visual parameters.";

    resultTheme.textContent = String(
      data.theme || "surprise"
    ).replaceAll("-", " ");

    resultInput.textContent =
      data.input_source === "upload"
        ? "Audio file"
        : "Microphone";

    renderVoiceDna(data.voice_dna || {});

    if (data.artwork_url) {
      artworkImage.src = data.artwork_url;

      artworkImage.alt =
        `Artwork generated from ${
          data.input_source === "upload"
            ? "an audio file"
            : "your microphone recording"
        }`;

      artworkImage.hidden = false;
      artworkPlaceholder.hidden = true;

      downloadButton.href = data.artwork_url;
      downloadButton.download =
        `${data.artwork_id || "reotoi-artwork"}.svg`;

      downloadButton.classList.remove("is-disabled");
      downloadButton.removeAttribute("aria-disabled");
    } else {
      artworkImage.removeAttribute("src");
      artworkImage.hidden = true;

      artworkPlaceholder.hidden = false;

      downloadButton.removeAttribute("href");
      downloadButton.removeAttribute("download");

      downloadButton.classList.add("is-disabled");
      downloadButton.setAttribute(
        "aria-disabled",
        "true"
      );
    }

    saveButton.disabled =
      !data.artwork_id ||
      !data.artwork_url;

    resultSection.scrollIntoView({
      behavior: "smooth",
      block: "start",
    });
  }

  async function generate() {
    setError("");
    saveStatus.textContent = "";

    if (!source?.blob) {
      setError(
        "Record your voice or choose an audio file first."
      );
      return;
    }

    const form = new FormData();

    form.append(
      "audio",
      source.blob,
      source.filename || "reotoi-audio.wav"
    );

    form.append("theme", getTheme());
    form.append("input_source", source.type);

    generateButton.disabled = true;
    generateButton.textContent = "Creating…";

    recorderStatus.textContent =
      "Creating your artwork";

    recorderHelp.textContent =
      "Analyzing your voice and translating it into visual form…";

    try {
      const response = await fetch("/generate", {
        method: "POST",
        body: form,
      });

      const contentType =
        response.headers.get("content-type") || "";

      const payload =
        contentType.includes("application/json")
          ? await response.json()
          : null;

      if (!response.ok) {
        throw new Error(
          payload?.detail ||
          "reotoi could not create the artwork. Please try again."
        );
      }

      if (!payload?.success) {
        throw new Error(
          "reotoi could not create the artwork. Please try again."
        );
      }

      showResult(payload);

      recorderStatus.textContent =
        source.type === "upload"
          ? "Audio file processed"
          : "Recording processed";

      recorderHelp.textContent =
        "Your voice has shaped the artwork shown below.";
    } catch (error) {
      console.error(
        "Artwork generation failed",
        error
      );

      setError(
        error instanceof Error
          ? error.message
          : "reotoi could not create the artwork. Please try again."
      );

      recorderStatus.textContent =
        "Artwork not created";

      recorderHelp.textContent =
        "Check the error message and try again.";
    } finally {
      generateButton.textContent =
        "Generate artwork";

      updateGenerateState();
    }
  }

  generateButton.addEventListener(
    "click",
    generate
  );

  saveButton.addEventListener(
    "click",
    async () => {
      if (
        !latestResult?.artwork_id ||
        !latestResult?.artwork_url
      ) {
        return;
      }

      saveButton.disabled = true;
      saveStatus.textContent = "Saving…";

      const form = new FormData();

      form.append(
        "artwork_id",
        latestResult.artwork_id
      );

      form.append(
        "artwork_url",
        latestResult.artwork_url
      );

      form.append(
        "theme",
        latestResult.theme || getTheme()
      );

      form.append(
        "voice_dna",
        JSON.stringify(
          latestResult.voice_dna || {}
        )
      );

      try {
        const response = await fetch(
          "/gallery/save",
          {
            method: "POST",
            body: form,
          }
        );

        const contentType =
          response.headers.get("content-type") ||
          "";

        const payload =
          contentType.includes("application/json")
            ? await response.json()
            : null;

        if (!response.ok) {
          throw new Error(
            payload?.detail ||
            "The artwork could not be saved."
          );
        }

        saveStatus.textContent = payload?.success
          ? "Saved to your gallery."
          : "The artwork could not be saved.";
      } catch (error) {
        saveStatus.textContent =
          error instanceof Error
            ? error.message
            : "The artwork could not be saved.";
      } finally {
        saveButton.disabled = false;
      }
    }
  );

  downloadButton.addEventListener(
    "click",
    (event) => {
      if (
        !downloadButton.href ||
        downloadButton.classList.contains("is-disabled")
      ) {
        event.preventDefault();
      }
    }
  );

  updateGenerateState();
})();
