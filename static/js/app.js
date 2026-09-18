(() => {
  "use strict";

  const root = document.querySelector(".creation-page");
  if (!root) return;

  const maxSeconds = Number(root.dataset.maxSeconds || 30);
  const maxInputBytes = 20 * 1024 * 1024;
  const maxServerBytes = 4 * 1024 * 1024;
  const targetSampleRate = 16000;

  // Formats that libsndfile is expected to decode directly on the server.
  // The server ultimately checks the actual bytes, not these extensions.
  const serverDecodedExtensions = new Set([
    ".wav",
    ".wave",
    ".mp3",
    ".ogg",
    ".oga",
    ".flac",
    ".aif",
    ".aiff",
    ".au",
    ".snd",
  ]);

  // Containers/codecs that are normally better handled by the browser's
  // built-in media decoder, avoiding an external codec runtime.
  const browserDecodedExtensions = new Set([
    ".mp4",
    ".m4a",
    ".webm",
    ".aac",
  ]);

  const supportedExtensions = new Set([
    ...serverDecodedExtensions,
    ...browserDecodedExtensions,
  ]);

  const mimeToExtension = {
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/ogg": ".ogg",
    "application/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/wave": ".wav",
    "audio/x-wav": ".wav",
    "audio/flac": ".flac",
    "audio/aiff": ".aiff",
    "audio/x-aiff": ".aiff",
    "audio/mp4": ".mp4",
    "video/mp4": ".mp4",
    "audio/x-m4a": ".m4a",
    "audio/webm": ".webm",
    "video/webm": ".webm",
    "audio/aac": ".aac",
  };

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

  let recording = false;
  let preparingAudio = false;
  let source = null;
  let latestResult = null;
  let preparationToken = 0;

  if (audioFileInput) {
    audioFileInput.setAttribute(
      "accept",
      [
        "audio/*",
        "video/mp4",
        "video/webm",
        ...Array.from(supportedExtensions),
      ].join(",")
    );
  }

  function setError(message) {
    if (!formError) return;

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
    if (!generateButton) return;

    generateButton.disabled =
      !source?.blob || recording || preparingAudio;
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

    if (recorder) {
      recorder.classList.remove("is-recording");
    }

    if (recordLabel) {
      recordLabel.textContent = "Start speaking";
    }
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
    const numberOfChannels = 1;
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
    view.setUint16(22, numberOfChannels, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(
      28,
      sampleRate * numberOfChannels * bytesPerSample,
      true
    );
    view.setUint16(32, numberOfChannels * bytesPerSample, true);
    view.setUint16(34, 16, true);

    writeString(36, "data");
    view.setUint32(40, dataLength, true);

    let offset = 44;

    for (let i = 0; i < samples.length; i += 1) {
      const sample = Math.max(-1, Math.min(1, samples[i]));
      const pcm =
        sample < 0 ? sample * 0x8000 : sample * 0x7fff;

      view.setInt16(offset, pcm, true);
      offset += 2;
    }

    return new Blob([buffer], { type: "audio/wav" });
  }

  function getAudioContextConstructor() {
    return window.AudioContext || window.webkitAudioContext || null;
  }

  function getOfflineAudioContextConstructor() {
    return (
      window.OfflineAudioContext ||
      window.webkitOfflineAudioContext ||
      null
    );
  }

  function getFileExtension(file) {
    const name = file?.name || "";
    const dot = name.lastIndexOf(".");
    return dot === -1 ? "" : name.slice(dot).toLowerCase();
  }

  function getMimeType(file) {
    return (file?.type || "")
      .toLowerCase()
      .split(";", 1)[0]
      .trim();
  }

  function resolveExtension(file) {
    const extension = getFileExtension(file);

    if (supportedExtensions.has(extension)) {
      return extension;
    }

    return mimeToExtension[getMimeType(file)] || "";
  }

  function isSupportedInputFile(file) {
    const extension = resolveExtension(file);
    const mimeType = getMimeType(file);

    // Known formats are accepted by extension or MIME type.
    if (extension) return true;

    // Allow other browser-recognized audio types instead of rejecting them
    // just because their filename extension is unfamiliar.
    if (mimeType.startsWith("audio/")) return true;

    return mimeType === "video/mp4" || mimeType === "video/webm";
  }

  function canSendDirectlyToServer(file) {
    const extension = resolveExtension(file);
    return serverDecodedExtensions.has(extension);
  }

  async function normalizeAudioBuffer(audioBuffer) {
    const OfflineAudioContextCtor =
      getOfflineAudioContextConstructor();

    if (!OfflineAudioContextCtor) {
      throw new Error(
        "Your browser cannot prepare this audio file for analysis. Please use MP3, OGG, FLAC or WAV instead."
      );
    }

    if (!audioBuffer || !audioBuffer.length || !audioBuffer.duration) {
      throw new Error(
        "The selected file does not contain a usable audio track."
      );
    }

    if (audioBuffer.duration > maxSeconds + 0.25) {
      throw new Error(
        `Audio must be ${maxSeconds} seconds or less.`
      );
    }

    const frameCount = Math.max(
      1,
      Math.ceil(audioBuffer.duration * targetSampleRate)
    );

    const offlineContext = new OfflineAudioContextCtor(
      1,
      frameCount,
      targetSampleRate
    );

    const monoBuffer = offlineContext.createBuffer(
      1,
      audioBuffer.length,
      audioBuffer.sampleRate
    );

    const monoSamples = monoBuffer.getChannelData(0);
    const channelCount = Math.max(
      1,
      audioBuffer.numberOfChannels
    );

    for (let channel = 0; channel < channelCount; channel += 1) {
      const channelSamples = audioBuffer.getChannelData(channel);

      for (let index = 0; index < channelSamples.length; index += 1) {
        monoSamples[index] +=
          channelSamples[index] / channelCount;
      }
    }

    const bufferSource = offlineContext.createBufferSource();
    bufferSource.buffer = monoBuffer;
    bufferSource.connect(offlineContext.destination);
    bufferSource.start(0);

    const rendered = await offlineContext.startRendering();
    return rendered.getChannelData(0).slice();
  }

  async function decodeUploadedFile(file) {
    const AudioContextCtor = getAudioContextConstructor();

    if (!AudioContextCtor) {
      throw new Error(
        "Your browser cannot decode this audio file. Please use MP3, OGG, FLAC or WAV instead."
      );
    }

    let decodingContext = null;

    try {
      decodingContext = new AudioContextCtor();

      const arrayBuffer = await file.arrayBuffer();
      const decoded = await decodingContext.decodeAudioData(arrayBuffer);

      return await normalizeAudioBuffer(decoded);
    } catch (error) {
      console.error(
        "Browser audio decoding failed:",
        error
      );

      throw new Error(
        `reotoi could not decode ${
          file.name || "this audio file"
        }. The format or codec is not supported by this browser.`
      );
    } finally {
      if (decodingContext) {
        decodingContext.close().catch(() => {});
      }
    }
  }

  async function prepareBrowserAudio(file, token) {
    const normalizedSamples = await decodeUploadedFile(file);

    if (token !== preparationToken) {
      return null;
    }

    const wavBlob = encodeWav(
      normalizedSamples,
      targetSampleRate
    );

    if (wavBlob.size > maxServerBytes) {
      throw new Error(
        "The prepared audio is too large to process. Please choose a shorter audio file."
      );
    }

    return {
      type: "upload",
      blob: wavBlob,
      filename: `reotoi-upload-${Date.now()}.wav`,
      originalFilename: file.name || "audio file",
      contentType: "audio/wav",
      format: ".wav",
      normalizedFrom:
        resolveExtension(file) || getMimeType(file),
    };
  }

  async function prepareUploadedFile(file, token) {
    /*
     * MP3/OGG/FLAC/WAV and other libsndfile-compatible inputs can be sent as
     * supplied. audio_conversion.py identifies the format from the bytes, so
     * an incorrect MIME type or extension does not break decoding.
     */
    if (canSendDirectlyToServer(file)) {
      if (file.size > maxServerBytes) {
        throw new Error(
          "Please choose an audio file smaller than 4 MB for this format."
        );
      }

      return {
        type: "upload",
        blob: file,
        filename:
          file.name ||
          `reotoi-upload-${Date.now()}.audio`,
        originalFilename:
          file.name || "audio file",
        contentType: file.type || "",
        format: resolveExtension(file),
      };
    }

    /*
     * MP4/M4A/WebM/AAC and other browser-supported media are decoded in the
     * browser and converted to mono 16 kHz PCM WAV. No external codec runtime.
     */
    return prepareBrowserAudio(file, token);
  }

  async function prepareMicrophoneSamples(
    samples,
    sampleRate
  ) {
    const AudioContextCtor =
      getAudioContextConstructor();

    if (!AudioContextCtor) {
      throw new Error(
        "Your browser cannot prepare the microphone recording."
      );
    }

    const context =
      new AudioContextCtor();

    try {
      const buffer =
        context.createBuffer(
          1,
          samples.length,
          sampleRate
        );

      buffer.copyToChannel(
        samples,
        0
      );

      return await normalizeAudioBuffer(
        buffer
      );
    } finally {
      context.close().catch(() => {});
    }
  }

  async function startRecording() {
    setError("");

    preparationToken += 1;
    source = null;
    preparingAudio = false;

    if (audioFileInput) {
      audioFileInput.value = "";
    }

    if (fileName) {
      fileName.textContent = "";
    }

    recordedSamples = [];
    recordedSampleCount = 0;
    elapsed = 0;

    if (recorderTimer) {
      recorderTimer.textContent =
        formatTime(0);
    }

    updateGenerateState();

    const AudioContextCtor =
      getAudioContextConstructor();

    if (
      !navigator.mediaDevices?.getUserMedia ||
      !AudioContextCtor
    ) {
      if (recorderStatus) {
        recorderStatus.textContent =
          "Microphone unavailable";
      }

      if (recorderHelp) {
        recorderHelp.textContent =
          "Your browser cannot record from a microphone. Use an audio file instead.";
      }

      setError(
        "Microphone recording is not supported by this browser. Please use an audio file instead."
      );

      return;
    }

    try {
      audioContext =
        new AudioContextCtor();

      mediaStream =
        await navigator.mediaDevices.getUserMedia({
          audio: {
            channelCount: 1,
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
          },
        });

      await audioContext.resume();

      sourceNode =
        audioContext.createMediaStreamSource(
          mediaStream
        );

      processorNode =
        audioContext.createScriptProcessor(
          4096,
          1,
          1
        );

      silentGain =
        audioContext.createGain();

      silentGain.gain.value = 0;

      processorNode.onaudioprocess =
        (event) => {
          if (!recording) return;

          const input =
            event.inputBuffer.getChannelData(
              0
            );

          const copy =
            new Float32Array(
              input.length
            );

          copy.set(input);
          recordedSamples.push(copy);
          recordedSampleCount += copy.length;
        };

      sourceNode.connect(
        processorNode
      );

      processorNode.connect(
        silentGain
      );

      silentGain.connect(
        audioContext.destination
      );

      recording = true;

      if (recorder) {
        recorder.classList.add(
          "is-recording"
        );
      }

      if (recordLabel) {
        recordLabel.textContent =
          "Stop speaking";
      }

      if (recorderStatus) {
        recorderStatus.textContent =
          "Recording";
      }

      if (recorderHelp) {
        recorderHelp.textContent =
          `Speak naturally. Recording stops automatically at ${maxSeconds} seconds.`;
      }

      timerId =
        window.setInterval(() => {
          elapsed += 1;

          if (recorderTimer) {
            recorderTimer.textContent =
              formatTime(elapsed);
          }

          if (
            elapsed >=
            maxSeconds
          ) {
            void stopRecording();
          }
        }, 1000);
    } catch (error) {
      console.error(
        "Microphone initialization failed:",
        error
      );

      recording = false;

      cleanupAudioGraph();
      resetRecordingVisuals();

      if (recorderStatus) {
        recorderStatus.textContent =
          "Microphone unavailable";
      }

      if (recorderHelp) {
        recorderHelp.textContent =
          "Allow microphone access or use an audio file instead.";
      }

      setError(
        "reotoi could not access your microphone. Check your browser permission and try again, or use an audio file instead."
      );

      updateGenerateState();
    }
  }

  async function stopRecording() {
    if (
      !recording ||
      preparingAudio
    ) {
      return;
    }

    recording = false;
    stopTimer();

    const sampleRate =
      audioContext?.sampleRate ||
      44100;

    const samples =
      mergeBuffers(
        recordedSamples,
        recordedSampleCount
      );

    const duration =
      samples.length /
      sampleRate;

    cleanupAudioGraph();
    resetRecordingVisuals();

    if (
      !samples.length ||
      duration < 0.2
    ) {
      if (recorderStatus) {
        recorderStatus.textContent =
          "No usable recording";
      }

      if (recorderHelp) {
        recorderHelp.textContent =
          "Please speak for a little longer and try again.";
      }

      setError(
        "The microphone recording was too short. Please record again."
      );

      updateGenerateState();
      return;
    }

    if (
      duration >
      maxSeconds + 0.25
    ) {
      setError(
        `Audio must be ${maxSeconds} seconds or less.`
      );

      updateGenerateState();
      return;
    }

    const token =
      ++preparationToken;

    preparingAudio = true;
    updateGenerateState();

    if (recorderStatus) {
      recorderStatus.textContent =
        "Preparing recording";
    }

    if (recorderHelp) {
      recorderHelp.textContent =
        "Preparing the recording for voice analysis…";
    }

    try {
      const normalizedSamples =
        await prepareMicrophoneSamples(
          samples,
          sampleRate
        );

      if (
        token !==
        preparationToken
      ) {
        return;
      }

      const wavBlob =
        encodeWav(
          normalizedSamples,
          targetSampleRate
        );

      if (
        wavBlob.size >
        maxServerBytes
      ) {
        throw new Error(
          "The prepared recording is too large to process. Please record a shorter sample."
        );
      }

      source = {
        type: "microphone",
        blob: wavBlob,
        filename:
          `reotoi-${Date.now()}.wav`,
        contentType:
          "audio/wav",
        format: ".wav",
      };

      if (recorderStatus) {
        recorderStatus.textContent =
          "Recording ready";
      }

      if (recorderHelp) {
        recorderHelp.textContent =
          "Your microphone recording is ready to become artwork.";
      }
    } catch (error) {
      if (
        token !==
        preparationToken
      ) {
        return;
      }

      console.error(
        "Microphone preparation failed:",
        error
      );

      source = null;

      setError(
        error instanceof Error
          ? error.message
          : "reotoi could not prepare the microphone recording. Please try again."
      );

      if (recorderStatus) {
        recorderStatus.textContent =
          "Recording unavailable";
      }

      if (recorderHelp) {
        recorderHelp.textContent =
          "Please record again or use an audio file instead.";
      }
    } finally {
      if (
        token ===
        preparationToken
      ) {
        preparingAudio = false;
        updateGenerateState();
      }
    }
  }

  if (recordButton) {
    recordButton.addEventListener(
      "click",
      () => {
        if (recording) {
          void stopRecording();
        } else {
          void startRecording();
        }
      }
    );
  }

  if (audioFileInput) {
    audioFileInput.addEventListener(
      "change",
      async () => {
        setError("");

        const file =
          audioFileInput.files?.[0];

        if (!file) return;

        const token =
          ++preparationToken;

        recording = false;
        preparingAudio = false;

        stopTimer();
        cleanupAudioGraph();
        resetRecordingVisuals();

        source = null;

        updateGenerateState();

        if (
          !isSupportedInputFile(file)
        ) {
          if (fileName) {
            fileName.textContent =
              "";
          }

          if (recorderStatus) {
            recorderStatus.textContent =
              "Unsupported audio file";
          }

          if (recorderHelp) {
            recorderHelp.textContent =
              "Choose an MP3, OGG, FLAC, WAV, MP4/M4A, WebM or another browser-supported audio file.";
          }

          setError(
            "This file type is not supported by reotoi. Choose an audio format your browser or reotoi can decode."
          );

          return;
        }

        if (
          file.size >
          maxInputBytes
        ) {
          if (fileName) {
            fileName.textContent =
              "";
          }

          if (recorderStatus) {
            recorderStatus.textContent =
              "Audio file unavailable";
          }

          if (recorderHelp) {
            recorderHelp.textContent =
              "Choose a file smaller than 20 MB or use the microphone instead.";
          }

          setError(
            "Please choose an audio file smaller than 20 MB."
          );

          return;
        }

        if (fileName) {
          fileName.textContent =
            file.name;
        }

        preparingAudio = true;
        updateGenerateState();

        const directToServer =
          canSendDirectlyToServer(
            file
          );

        if (recorderStatus) {
          recorderStatus.textContent =
            directToServer
              ? "Audio file ready"
              : "Preparing audio file";
        }

        if (recorderHelp) {
          recorderHelp.textContent =
            directToServer
              ? "The audio file is ready to be analyzed."
              : "Converting the audio track to a format reotoi can analyze…";
        }

        try {
          const prepared =
            await prepareUploadedFile(
              file,
              token
            );

          if (
            token !==
              preparationToken ||
            !prepared
          ) {
            return;
          }

          source = prepared;

          if (recorderStatus) {
            recorderStatus.textContent =
              "Audio file ready";
          }

          if (recorderHelp) {
            recorderHelp.textContent =
              "The audio file is ready to be analyzed.";
          }
        } catch (error) {
          if (
            token !==
            preparationToken
          ) {
            return;
          }

          console.error(
            "Audio file preparation failed:",
            error
          );

          source = null;

          if (recorderStatus) {
            recorderStatus.textContent =
              "Audio file unavailable";
          }

          if (recorderHelp) {
            recorderHelp.textContent =
              "Try another supported file or use the microphone instead.";
          }

          setError(
            error instanceof Error
              ? error.message
              : "reotoi could not prepare this audio file. Please try another file."
          );
        } finally {
          if (
            token ===
            preparationToken
          ) {
            preparingAudio = false;
            updateGenerateState();
          }
        }
      }
    );
  }

  function renderVoiceDna(dna) {
    if (!voiceDnaBars) return;

    voiceDnaBars.innerHTML = "";

    const labels = [
      "pitch",
      "energy",
      "rhythm",
      "variation",
      "pause",
    ];

    labels.forEach((key) => {
      const value =
        Number(dna?.[key] ?? 0);

      const clamped =
        Math.max(
          0,
          Math.min(10, value)
        );

      const row =
        document.createElement(
          "div"
        );

      row.className =
        "dna-row";

      const label =
        document.createElement(
          "span"
        );

      label.className =
        "dna-row__label";

      label.textContent =
        key.charAt(0).toUpperCase() +
        key.slice(1);

      const track =
        document.createElement(
          "span"
        );

      track.className =
        "dna-row__track";

      track.setAttribute(
        "aria-hidden",
        "true"
      );

      const fill =
        document.createElement(
          "span"
        );

      fill.className =
        "dna-row__fill";

      fill.style.width =
        `${clamped * 10}%`;

      track.appendChild(fill);

      const displayValue =
        document.createElement(
          "span"
        );

      displayValue.className =
        "dna-row__value";

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

    if (resultSection) {
      resultSection.hidden =
        false;
    }

    if (resultMessage) {
      resultMessage.textContent =
        data.message ||
        "Your voice has been translated into visual form.";
    }

    if (resultTheme) {
      resultTheme.textContent =
        String(
          data.theme ||
            "surprise"
        ).replaceAll(
          "-",
          " "
        );
    }

    if (resultInput) {
      resultInput.textContent =
        data.input_source ===
        "upload"
          ? "Audio file"
          : "Microphone";
    }

    renderVoiceDna(
      data.voice_dna || {}
    );

    if (
      data.artwork_url &&
      artworkImage &&
      artworkPlaceholder
    ) {
      artworkImage.src =
        data.artwork_url;

      artworkImage.alt =
        data.input_source ===
        "upload"
          ? "Artwork generated from the uploaded audio file"
          : "Artwork generated from the microphone recording";

      artworkImage.hidden =
        false;

      artworkPlaceholder.hidden =
        true;

      if (downloadButton) {
        downloadButton.href =
          data.artwork_url;

        downloadButton.download =
          `${
            data.artwork_id ||
            "reotoi-artwork"
          }.svg`;

        downloadButton.classList.remove(
          "is-disabled"
        );

        downloadButton.removeAttribute(
          "aria-disabled"
        );
      }
    } else {
      if (artworkImage) {
        artworkImage.removeAttribute(
          "src"
        );

        artworkImage.hidden =
          true;
      }

      if (artworkPlaceholder) {
        artworkPlaceholder.hidden =
          false;
      }

      if (downloadButton) {
        downloadButton.removeAttribute(
          "href"
        );

        downloadButton.removeAttribute(
          "download"
        );

        downloadButton.classList.add(
          "is-disabled"
        );

        downloadButton.setAttribute(
          "aria-disabled",
          "true"
        );
      }
    }

    if (saveButton) {
      saveButton.disabled =
        !data.artwork_id ||
        !data.artwork_url;
    }

    if (resultSection) {
      resultSection.scrollIntoView({
        behavior: "smooth",
        block: "start",
      });
    }
  }

  async function parseResponse(
    response
  ) {
    const contentType =
      response.headers.get(
        "content-type"
      ) || "";

    if (
      contentType.includes(
        "application/json"
      )
    ) {
      return response.json();
    }

    const text =
      await response.text();

    return {
      success: false,
      detail:
        text ||
        "reotoi could not process the request.",
    };
  }

  async function generate() {
    setError("");

    if (saveStatus) {
      saveStatus.textContent =
        "";
    }

    if (!source?.blob) {
      setError(
        "Record your voice or choose an audio file first."
      );

      return;
    }

    const form =
      new FormData();

    form.append(
      "audio",
      source.blob,
      source.filename ||
        "reotoi-audio"
    );

    form.append(
      "theme",
      getTheme()
    );

    form.append(
      "input_source",
      source.type
    );

    if (generateButton) {
      generateButton.disabled =
        true;

      generateButton.textContent =
        "Creating…";
    }

    if (recorderStatus) {
      recorderStatus.textContent =
        "Creating your artwork";
    }

    if (recorderHelp) {
      recorderHelp.textContent =
        "Analyzing your voice and translating it into visual form…";
    }

    try {
      const response =
        await fetch(
          "/generate",
          {
            method: "POST",
            body: form,
          }
        );

      const payload =
        await parseResponse(
          response
        );

      if (!response.ok) {
        throw new Error(
          payload.detail ||
            "reotoi could not create the artwork. Please try again."
        );
      }

      if (!payload.success) {
        throw new Error(
          payload.detail ||
            "reotoi could not create the artwork. Please try again."
        );
      }

      showResult(payload);

      if (recorderStatus) {
        recorderStatus.textContent =
          source.type ===
          "upload"
            ? "Audio file processed"
            : "Recording processed";
      }

      if (recorderHelp) {
        recorderHelp.textContent =
          "Your voice has shaped the artwork shown below.";
      }
    } catch (error) {
      console.error(
        "Artwork generation failed:",
        error
      );

      setError(
        error instanceof Error
          ? error.message
          : "reotoi could not create the artwork. Please try again."
      );

      if (recorderStatus) {
        recorderStatus.textContent =
          "Artwork not created";
      }

      if (recorderHelp) {
        recorderHelp.textContent =
          "Check the error message and try again.";
      }
    } finally {
      if (generateButton) {
        generateButton.textContent =
          "Generate artwork";
      }

      updateGenerateState();
    }
  }

  if (generateButton) {
    generateButton.addEventListener(
      "click",
      () => {
        void generate();
      }
    );
  }

  if (saveButton) {
    saveButton.addEventListener(
      "click",
      async () => {
        if (
          !latestResult?.artwork_id ||
          !latestResult?.artwork_url
        ) {
          return;
        }

        saveButton.disabled =
          true;

        if (saveStatus) {
          saveStatus.textContent =
            "Saving…";
        }

        const form =
          new FormData();

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
          latestResult.theme ||
            getTheme()
        );

        form.append(
          "voice_dna",
          JSON.stringify(
            latestResult.voice_dna ||
              {}
          )
        );

        try {
          const response =
            await fetch(
              "/gallery/save",
              {
                method: "POST",
                body: form,
              }
            );

          const payload =
            await parseResponse(
              response
            );

          if (!response.ok) {
            throw new Error(
              payload.detail ||
                "The artwork could not be saved."
            );
          }

          if (saveStatus) {
            saveStatus.textContent =
              payload.success
                ? "Saved to your gallery."
                : "The artwork could not be saved.";
          }
        } catch (error) {
          console.error(
            "Gallery save failed:",
            error
          );

          if (saveStatus) {
            saveStatus.textContent =
              error instanceof Error
                ? error.message
                : "The artwork could not be saved.";
          }
        } finally {
          saveButton.disabled =
            false;
        }
      }
    );
  }

  if (downloadButton) {
    downloadButton.addEventListener(
      "click",
      (event) => {
        if (
          !downloadButton.href ||
          downloadButton.classList.contains(
            "is-disabled"
          )
        ) {
          event.preventDefault();
        }
      }
    );
  }

  updateGenerateState();
})();
