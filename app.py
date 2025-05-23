import queue
import asyncio 
import base64
import os
from google.cloud import speech
import re
import sys
import time


from fastapi import FastAPI,WebSocket,WebSocketException

from fastapi.responses import HTMLResponse


os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "stt_credentials.json"

app = FastAPI()

# Audio recording parameters
STREAMING_LIMIT = 20000 # 4 minutes
SAMPLE_RATE = 16000
CHUNK_SIZE = int(SAMPLE_RATE / 10)  # 100ms

RED = "\033[0;31m"
GREEN = "\033[0;32m"
YELLOW = "\033[0;33m"

is_stream_limit_up = False
def get_current_time() -> int:
    """Return Current Time in MS.

    Returns:
        int: Current Time in MS.
    """

    return int(round(time.time() * 1000))


class AudioStream:
    """Opens a recording stream as a generator yielding the audio chunks."""

    def __init__(
        self: object,
    ) -> None:
        """Creates a resumable microphone stream.

        Args:
        self: The class instance.
        rate: The audio file's sampling rate.
        chunk_size: The audio file's chunk size.

        returns: None
        """
        self._buff = queue.Queue()
        self.closed = True
        self.start_time = get_current_time()
        self.audio_input = []   
        self.result_end_time = 0
        self.is_final_end_time = 0
        self.last_transcript_was_final = False

    def __enter__(self: object) -> object:
        """Opens the stream.

        Args:
        self: The class instance.

        returns: None
        """
        self.closed = False
        return self
    
    def __exit__(
        self: object,
        type: object,
        value: object,
        traceback: object,
    ) -> object:
        """Closes the stream and releases resources.

        Args:
        self: The class instance.
        type: The exception type.
        value: The exception value.
        traceback: The exception traceback.

        returns: None
        """
        self.closed = True
        # Signal the generator to terminate so that the client's
        # streaming_recognize method will not block the process termination.
        self._buff.put(None)

    def fill_buffer(
        self: object,
        in_data: object,
        *args: object,
        **kwargs: object,
    ) -> None:
        """Continuously collect data from the audio stream, into the buffer.

        Args:
        self: The class instance.
        in_data: The audio data as a bytes object.
        args: Additional arguments.
        kwargs: Additional arguments.

        returns: None
        """
        self._buff.put(in_data)

    def generator(self: object) -> object:
        """Stream Audio from microphone to API and to local buffer

        Args:
            self: The class instance.

        returns:
            The data from the audio stream.
        """
        while not self.closed:
            data = []

            # Use a blocking get() to ensure there's at least one chunk of
            # data, and stop iteration if the chunk is None, indicating the
            # end of the audio stream.
            chunk = self._buff.get()
            self.audio_input.append(chunk)

            if chunk is None:
                return
            data.append(chunk)
            # Now consume whatever other data's still buffered.
            while True:
                try:
                    chunk = self._buff.get(block=False)

                    if chunk is None:
                        return
                    data.append(chunk)
                    self.audio_input.append(chunk)

                except queue.Empty:
                    break
            yield b"".join(data)


async def listen_print_loop(responses: object, stream: object,websocket) -> None:
    """Iterates through server responses and prints them.

    The responses passed is a generator that will block until a response
    is provided by the server.

    Each response may contain multiple results, and each result may contain
    multiple alternatives; for details, see https://goo.gl/tjCPAU.  Here we
    print only the transcription for the top alternative of the top result.

    In this case, responses are provided for interim results as well. If the
    response is an interim one, print a line feed at the end of it, to allow
    the next result to overwrite it, until the response is a final one. For the
    final one, print a newline to preserve the finalized transcription.

    Arg:
        responses: The responses returned from the API.
        stream: The audio stream to be processed.
    """
    async for response in responses:
        if get_current_time() - stream.start_time > STREAMING_LIMIT:
            stream.start_time = get_current_time()
            await websocket.send_json({
                "type":"error",
                "message":"Time limit of 4 minutes reached",
                "is_time_up":True
            })
            return

        if not response.results:
            continue

        result = response.results[0]

        if not result.alternatives:
            continue

        transcript = result.alternatives[0].transcript

        result_seconds = 0
        result_micros = 0

        if result.result_end_time.seconds:
            result_seconds = result.result_end_time.seconds

        if result.result_end_time.microseconds:
            result_micros = result.result_end_time.microseconds

        stream.result_end_time = int((result_seconds * 1000) + (result_micros / 1000))

        corrected_time = stream.result_end_time
        # Display interim results, but with a carriage return at the end of the
        # line, so subsequent lines will overwrite them.

        if result.is_final:
            sys.stdout.write(GREEN)
            sys.stdout.write("\033[K")
            sys.stdout.write(str(corrected_time) + ": " + transcript + "\n")
            await websocket.send_json({
                "type":"transcript",
                "text":transcript,
                "is_final":True
            })

            stream.is_final_end_time = stream.result_end_time
            stream.last_transcript_was_final = True
        else:
            sys.stdout.write(RED)
            sys.stdout.write("\033[K")
            sys.stdout.write(str(corrected_time) + ": " + transcript + "\r")
            # await websocket.send_json({
            #     "type":"transcript",
            #     "text":transcript,
            #     "is_final":False
            # })
            stream.last_transcript_was_final = False



# app.mount("/static", StaticFiles(directory="static"), name="static")
@app.get("/")
async def root():
#     html = """<!DOCTYPE html>
# <html>
# <head>
#     <title>Websocket Audio Stream</title>
# </head>
# <body>
#     <h1>Websocket Audio Stream</h1>
#     <button id="startButton">Start Recording</button>
#     <button id="stopButton" disabled>Stop Recording</button>
#     <p id="status">Click 'Start Recording' to begin.</p>

#     <div style="border: 1px solid #ccc; padding: 10px; width: 80%; height: 150px; overflow-y: auto; white-space: pre-wrap; background: #f9f9f9;">
#         <strong>Live Transcription:</strong>
#         <div id="transcriptionBox"></div> 
#     </div>
    
#     <script>
#         const startButton = document.getElementById('startButton');
#         const stopButton = document.getElementById('stopButton');
#         const statusDisplay = document.getElementById('status');
#         const transcriptionDisplay = document.getElementById('transcriptionBox');
#         let websocket;
#         const sampleRate = 16000;

#         startButton.onclick = async () => {
#             statusDisplay.textContent = 'Recording...';
#             startButton.disabled = true;
#             stopButton.disabled = false;
#             try {
#                 const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
#                 const audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: sampleRate });
#                 const source = audioContext.createMediaStreamSource(stream);
#                 const scriptNode = audioContext.createScriptProcessor(4096, 1, 1);

#                 scriptNode.onaudioprocess = (audioProcessingEvent) => {
#                     if (websocket && websocket.readyState === WebSocket.OPEN) {
#                         const inputBuffer = audioProcessingEvent.inputBuffer.getChannelData(0);
#                         const int16Array = new Int16Array(inputBuffer.length);
#                         for (let i = 0; i < inputBuffer.length; i++) {
#                             const s = Math.max(-1, Math.min(1, inputBuffer[i]));
#                             int16Array[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
#                         }
#                         websocket.send(int16Array.buffer);
#                     }
#                 };
#                 source.connect(scriptNode);
#                 scriptNode.connect(audioContext.destination);

#                 websocket = new WebSocket('ws://127.0.0.1:8000/stt');

#                 websocket.onopen = () => {
#                     console.log('WebSocket connection established.');
#                     statusDisplay.textContent = 'Recording and sending audio...';
#                 };

#                 websocket.onmessage = (event) => {
#                     const message = event.data;
#                     const div = document.createElement("div");
#                     div.textContent = message;
#                     transcriptionDisplay.appendChild(div);

#                     // ✅ Scroll to bottom automatically
#                     transcriptionDisplay.parentElement.scrollTop = transcriptionDisplay.parentElement.scrollHeight;
#                 }

#                 websocket.onclose = () => {
#                     console.log('WebSocket connection closed.');
#                     statusDisplay.textContent = 'Recording stopped.';
#                     startButton.disabled = false;
#                     stopButton.disabled = true;
#                     location.reload();
#                 };
#                 websocket.onerror = (error) => {
#                     console.error('WebSocket error:', error);
#                     statusDisplay.textContent = 'WebSocket error occurred.';
#                     startButton.disabled = false;
#                     stopButton.disabled = true;
#                 };
#                 stopButton.onclick = () => {
#                     if (audioContext.state === 'running') {
#                         audioContext.close();
#                     }
#                     if (websocket && websocket.readyState === WebSocket.OPEN) {
#                         websocket.close();
#                     }
#                     statusDisplay.textContent = 'Stopping recording...';
#                     startButton.disabled = false;
#                     stopButton.disabled = true;
#                 };
#             } catch (err) {
#                 console.error('Error accessing microphone:', err);
#                 statusDisplay.textContent = 'Error accessing microphone.';
#                 startButton.disabled = false;
#                 stopButton.disabled = true;
#             }
#         };
#     </script>
# </body>
# </html>"""


    html = """
</head>
<body>
    <h1>Websocket Audio Stream</h1>
    <button id="startButton">Start Recording</button>
    <button id="stopButton" disabled>Stop Recording</button>
    <p id="status">Click 'Start Recording' to begin.</p>

    <div style="border: 1px solid #ccc; padding: 10px; width: 80%; height: 150px; overflow-y: auto; white-space: pre-wrap; background: #f9f9f9;">
        <strong>Live Transcription:</strong>
        <div id="transcriptionBox"></div> 
    </div>
    
    <script>
        const startButton = document.getElementById('startButton');
        const stopButton = document.getElementById('stopButton');
        const statusDisplay = document.getElementById('status');
        const transcriptionDisplay = document.getElementById('transcriptionBox');

        const sampleRate = 16000;
        const RECONNECT_DELAY = 100; // 100ms delay for reconnection
        let websocket;
        let audioContext, scriptNode, source;
        let userStopped = false;
        let isRecording = false; // Prevent multiple startRecording calls

        function cleanupAudio() {
            if (scriptNode) {
                scriptNode.onaudioprocess = null;
                scriptNode.disconnect();
                scriptNode = null;
            }
            if (source) {
                source.disconnect();
                source = null;
            }
            if (audioContext && audioContext.state !== 'closed') {
                audioContext.close();
                audioContext = null;
            }
        }

        function startRecording() {
            if (userStopped) {
                statusDisplay.textContent = 'Recording stopped by user.';
                startButton.disabled = false;
                stopButton.disabled = true;
                cleanupAudio();
                isRecording = false;
                return;
            }

            if (isRecording) {
                console.log('Recording already in progress, ignoring start attempt.');
                return;
            }

            isRecording = true;
            statusDisplay.textContent = 'Recording...';
            startButton.disabled = true;
            stopButton.disabled = false;

            try {
                navigator.mediaDevices.getUserMedia({ audio: true, video: false }).then((stream) => {
                    audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: sampleRate });
                    source = audioContext.createMediaStreamSource(stream);
                    scriptNode = audioContext.createScriptProcessor(4096, 1, 1);

                    scriptNode.onaudioprocess = (audioProcessingEvent) => {
                        if (websocket && websocket.readyState === WebSocket.OPEN) {
                            const inputBuffer = audioProcessingEvent.inputBuffer.getChannelData(0);
                            const int16Array = new Int16Array(inputBuffer.length);
                            for (let i = 0; i < inputBuffer.length; i++) {
                                const s = Math.max(-1, Math.min(1, inputBuffer[i]));
                                int16Array[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
                            }
                            console.log('Sending audio chunk, length:', int16Array.length);
                            websocket.send(int16Array.buffer);
                        }
                    };

                    source.connect(scriptNode);
                    scriptNode.connect(audioContext.destination);

                    websocket = new WebSocket('wss://virtue-output-mark-qualified.trycloudflare.com/stt');

                    websocket.onopen = () => {
                        console.log('WebSocket connection established.');
                        statusDisplay.textContent = 'Recording and sending audio...';
                    };

                    websocket.onmessage = (event) => {
                        try {
                            const data = JSON.parse(event.data);
                            if (data.type === "error" && data.is_time_up === true) {
                                console.warn("Time limit reached, reconnecting...");
                                cleanupAudio();
                                websocket.close();
                                if (!userStopped) {
                                    setTimeout(startRecording, RECONNECT_DELAY);
                                }
                                return;
                            } else if (data.type === "error") {
                                console.warn("Server error:", data.message);
                                const div = document.createElement("div");
                                div.textContent = `Error: ${data.message}`;
                                div.style.color = 'red';
                                transcriptionDisplay.appendChild(div);
                                transcriptionDisplay.parentElement.scrollTop = transcriptionDisplay.parentElement.scrollHeight;
                                cleanupAudio();
                                websocket.close();
                                if (!userStopped) {
                                    setTimeout(startRecording, RECONNECT_DELAY);
                                }
                                return;
                            } else if (data.type === "transcript") {
                                const div = document.createElement("div");
                                div.textContent = data.text;
                                if (data.is_final) {
                                    div.style.color = 'green';
                                } else {
                                    div.style.color = 'red';
                                }
                                transcriptionDisplay.appendChild(div);
                                transcriptionDisplay.parentElement.scrollTop = transcriptionDisplay.parentElement.scrollHeight;
                            } else {
                                const div = document.createElement("div");
                                div.textContent = JSON.stringify(data);
                                transcriptionDisplay.appendChild(div);
                                transcriptionDisplay.parentElement.scrollTop = transcriptionDisplay.parentElement.scrollHeight;
                            }
                        } catch (e) {
                            console.warn("Non-JSON message received:", event.data);
                            const div = document.createElement("div");
                            div.textContent = event.data;
                            transcriptionDisplay.appendChild(div);
                            transcriptionDisplay.parentElement.scrollTop = transcriptionDisplay.parentElement.scrollHeight;
                        }
                    };

                    websocket.onclose = () => {
                        console.log('WebSocket connection closed.');
                        isRecording = false;
                        if (!userStopped) {
                            statusDisplay.textContent = 'Reconnecting...';
                            setTimeout(startRecording, RECONNECT_DELAY);
                        } else {
                            statusDisplay.textContent = 'Recording stopped.';
                            startButton.disabled = false;
                            stopButton.disabled = true;
                            cleanupAudio();
                        }
                    };

                    websocket.onerror = (error) => {
                        console.error('WebSocket error:', error);
                        statusDisplay.textContent = 'WebSocket error occurred.';
                        startButton.disabled = false;
                        stopButton.disabled = true;
                        cleanupAudio();
                        isRecording = false;
                    };
                }).catch((err) => {
                    console.error('Error accessing microphone:', err);
                    statusDisplay.textContent = 'Error accessing microphone.';
                    startButton.disabled = false;
                    stopButton.disabled = true;
                    isRecording = false;
                });
            } catch (err) {
                console.error('Error setting up recording:', err);
                statusDisplay.textContent = 'Error setting up recording.';
                startButton.disabled = false;
                stopButton.disabled = true;
                isRecording = false;
            }
        }

        startButton.onclick = () => {
            userStopped = false;
            startRecording();
        };

        stopButton.onclick = () => {
            userStopped = true;
            cleanupAudio();
            if (websocket && websocket.readyState === WebSocket.OPEN) {
                websocket.close();
            }
            statusDisplay.textContent = 'Recording stopped.';
            startButton.disabled = false;
            stopButton.disabled = true;
            isRecording = false;
        };
    </script>
</body>
</html>
"""
    return HTMLResponse(html)


async def receive_and_yield_audio_chunks(websocket,stream):
    audio_generator = stream.generator()
    while True:
        audio_chunk = await websocket.receive_bytes()
        stream.fill_buffer(audio_chunk)
        yield next(audio_generator)

async def request_generator(audio_chunks,streaming_config):
    yield speech.StreamingRecognizeRequest(streaming_config=streaming_config)
    
    async for chunk in audio_chunks:
        yield speech.StreamingRecognizeRequest(audio_content=chunk)


@app.websocket("/stt")
async def main(websocket:WebSocket) -> None:
    try:
        await websocket.accept()
        """start bidirectional streaming from microphone input to speech API"""
        # client = speech.SpeechClient()
        client = speech.SpeechAsyncClient()
        config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=SAMPLE_RATE,
            language_code="en-US",
            max_alternatives=1,
        )

        streaming_config = speech.StreamingRecognitionConfig(
            config=config, interim_results=True
        )

        audio_handler = AudioStream()
        # await receive_audio(websocket,mic_manager)
        # print(mic_manager.chunk_size)
        sys.stdout.write(YELLOW)
        sys.stdout.write('\nListening......\n\n')
        sys.stdout.write("End (ms)       Transcript Results/Status\n")
        sys.stdout.write("=====================================================\n")

        with audio_handler as stream:
            sys.stdout.write(YELLOW)
            sys.stdout.write("0: NEW REQUEST\n")

            stream.audio_input = []
            audio_chunks = receive_and_yield_audio_chunks(websocket,stream)
            requests = request_generator(audio_chunks,streaming_config)

            responses = await client.streaming_recognize(requests=requests)

                # Now, put the transcription responses to use.
            await listen_print_loop(responses, stream,websocket)

            if stream.result_end_time > 0:
                stream.is_final_end_time = stream.result_end_time
            stream.result_end_time = 0
            stream.audio_input = []
                

            if not stream.last_transcript_was_final:
                sys.stdout.write("\n")
        
        print("Websocket closed from server")
        await websocket.close()
    
    except (Exception,asyncio.CancelledError) as err:
        print(f"Error Occured: {err}")
        try:
            await websocket.send_json({
                "type":"error",
                "message":str(err),
            })
            await websocket.close()
        except Exception as e:
            print(e)

# if __name__ == "__main__":
#     main()