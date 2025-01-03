import time
import wave
from concurrent.futures import ThreadPoolExecutor
import random

import joblib
import pygame
import pygame.freetype
import pyaudio
import matplotlib.animation as animation
import matplotlib.pyplot as plt
import numpy as np
import tensorflow.compat.v1 as tf
import pygame_gui
from tkinter import *
import concurrent.futures

from vggish_based_model.model import vggish_postprocess, vggish_params, vggish_slim, vggish_input
import pandas as pd
from df.enhance import enhance, init_df, load_audio, save_audio
import queue

# ###########################################################################################
# If there's an issue with the microphone, find the index of the microphone you want to use in the console,
# along with its sampleRate. Then, change the variable RATE below and add the parameter
# input_device_index=INDEX_OF_MICROPHONE
# to
# self.stream = self.p.open(..., input_device_index=INDEX_OF_MICROPHONE)
# ###########################################################################################
AUDIO_CHUNK = 1024
PLOT_CHUNK = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 2
RATE = 44100
CHUNK_SIZE = int(RATE * 0.5) * 2

vggish_checkpoint_path = 'model/vggish_model.ckpt'
CLASS_MODEL_PATH = 'model/trained_model_rf.pkl'
VGGISH_PARAMS_PATH = 'model/vggish_pca_params.npz'

pproc = vggish_postprocess.Postprocessor(VGGISH_PARAMS_PATH)
model, df_state, _ = init_df()

bonus = 1.15
noise_reduction = 10
noise_reduction_active = False


class SharedAudioResource:
    buffer = None
    pred_aud_buffer = queue.Queue()

    def __init__(self):
        self.p = pyaudio.PyAudio()
        for i in range(self.p.get_device_count()):
            print(self.p.get_device_info_by_index(i))
        self.stream = self.p.open(format=FORMAT, channels=CHANNELS, rate=RATE, input=True,
                                  frames_per_buffer=CHUNK_SIZE, input_device_index=4)
        self.read(AUDIO_CHUNK)

    def read(self, size):
        self.buffer = self.stream.read(CHUNK_SIZE, exception_on_overflow=False)
        return self.buffer

    def close(self):
        self.stream.stop_stream()
        self.stream.close()
        self.p.terminate()


def draw_text(text, pos, font, screen):
    text_surface, _ = font.render(text, (255, 255, 255))
    screen.blit(text_surface, (pos[0] - text_surface.get_width() // 2, pos[1] - text_surface.get_height() // 2))


def pygame_thread(audio):
    pygame.init()
    WIDTH, HEIGHT = 1366, 768
    manager = pygame_gui.UIManager((WIDTH, HEIGHT))
    FONT_SIZE = 24
    TEXT_POS = (WIDTH // 2, HEIGHT // 2 - 200)
    TEST_POS = (WIDTH // 2, HEIGHT // 2 - 300)
    NOISE_POS = (WIDTH // 2, HEIGHT // 2 + 100)

    screen = pygame.display.set_mode((WIDTH, HEIGHT))
    font = pygame.freetype.SysFont(None, FONT_SIZE)
    clock = pygame.time.Clock()

    global bonus, noise_reduction, noise_reduction_active

    running = True
    rf_classifier = joblib.load(CLASS_MODEL_PATH)
    with tf.Graph().as_default(), tf.Session() as sess:
        # Define VGGish
        embeddings = vggish_slim.define_vggish_slim()

        # Initialize all variables in the model, then load the VGGish checkpoint
        sess.run(tf.global_variables_initializer())
        vggish_slim.load_vggish_slim_checkpoint(sess, vggish_checkpoint_path)

        # Get the input tensor
        features_tensor = sess.graph.get_tensor_by_name(vggish_params.INPUT_TENSOR_NAME)
        while running:
            time_delta = clock.tick(60) / 1000.0
            start_time = time.time()
            buffer = []

            buffer.append(audio.read(AUDIO_CHUNK))

            buffer += buffer
            #buffer = buffer + buffer[:len(buffer)//2]

            wf = wave.open("temp.wav", 'wb')
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(audio.p.get_sample_size(FORMAT))
            wf.setframerate(RATE)
            wf.writeframes(b''.join(buffer))
            wf.close()
            audio1, _ = load_audio("temp.wav", sr=df_state.sr())
            if noise_reduction_active:
                audio1, _ = load_audio("temp.wav", sr=df_state.sr())
                enhanced = enhance(model, df_state, audio1, atten_lim_db=noise_reduction)
                save_audio("temp.wav", enhanced, df_state.sr())
            breathing_waveform = vggish_input.wavfile_to_examples("temp.wav")

            embedding_batch = np.array(sess.run(embeddings, feed_dict={features_tensor: breathing_waveform}))
            postprocessed_batch = pproc.postprocess(embedding_batch)
            df = pd.DataFrame(postprocessed_batch)

            prediction = rf_classifier.predict(df)
            audio.pred_aud_buffer.put((prediction[0], buffer))

            if prediction[0] == 0:
                screen.fill(color="red")
                draw_text(f"Inhale", TEXT_POS, font, screen)
            elif prediction[0] == 1:
                screen.fill(color="green")
                draw_text(f"Exhale", TEXT_POS, font, screen)
            else:
                screen.fill(color="blue")
                draw_text(f"Silence", TEXT_POS, font, screen)
            draw_text("Press SPACE to stop", TEST_POS, font, screen)

            print(time.time() - start_time)
            draw_text(f"Noise reduction: {noise_reduction}, active: {noise_reduction_active} ", NOISE_POS, font, screen)

            for event in pygame.event.get():
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_SPACE:
                        print("Exiting")
                        running = False

                manager.process_events(event)
            manager.update(time_delta)
            manager.draw_ui(screen)
            pygame.display.flip()
            clock.tick(60)

    pygame.quit()


plotdata = np.zeros((RATE * 2, 1))
predictions = np.zeros((RATE * 2, 1))
q = queue.Queue()
ymin = -1500
ymax = 1500
fig, ax = plt.subplots(figsize=(8, 4))
lines, = ax.plot(plotdata, color=(0, 1, 0.29))
ax.set_facecolor((0, 0, 0))
ax.set_ylim(ymin, ymax)
xes = [i for i in range(RATE * 2)]

fill_red = ax.fill_between(xes, ymin, ymax,
                           where=([True if predictions[i][0] == 0 else False for i in range(len(predictions))]),
                           color='red', alpha=0.3)
fill_green = ax.fill_between(xes, ymin, ymax,
                             where=([True if predictions[i][0] == 1 else False for i in range(len(predictions))]),
                             color='green', alpha=0.3)

fill_yellow = ax.fill_between(xes, ymin, ymax,
                              where=(
                                  [True if predictions[i][0] == 2 else False for i in range(len(predictions))]),
                              color='blue', alpha=0.3)


def update_plot(frame):
    global plotdata, predictions, fill_red, fill_green, fill_yellow, xes

    if q.empty():
        data = audio.pred_aud_buffer.get(block=True)
        chunks = np.array_split(data[1], 2)
        for chunk in chunks:
            q.put((data[0], chunk))

    queue_data = q.get()
    frames = np.frombuffer(queue_data[1], dtype=np.int16)
    frames = frames[::2]
    shift = len(frames)

    plotdata = np.roll(plotdata, -shift, axis=0)
    plotdata[-shift:, 0] = frames

    prediction = queue_data[0]
    predictions = np.roll(predictions, -shift, axis=0)
    pred_arr = [prediction for _ in range(shift)]
    predictions[-shift:, 0] = pred_arr

    lines.set_ydata(plotdata)

    fill_red.remove()
    fill_green.remove()
    fill_yellow.remove()

    fill_red = ax.fill_between(xes, ymin, ymax,
                               where=([True if predictions[i][0] == 0 else False for i in range(len(predictions))]),
                               color='red', alpha=0.3)
    fill_green = ax.fill_between(xes, ymin, ymax,
                                 where=([True if predictions[i][0] == 1 else False for i in range(len(predictions))]),
                                 color='green', alpha=0.3)
    fill_yellow = ax.fill_between(xes, ymin, ymax,
                                  where=([True if predictions[i][0] == 2 else False for i in range(len(predictions))]),
                                  color='blue', alpha=0.3)

    return lines, fill_red, fill_green, fill_yellow


def tkinker_sliders():
    root = Tk()
    root.title("Sliders")
    root.geometry("600x400")
    root.resizable(False, False)

    def set_bonus(val):
        global bonus
        bonus = float(val)

    def set_noise_reduction(val):
        global noise_reduction
        noise_reduction = float(val)

    bonus_label = Label(root, text="Bonus")
    bonus_label.pack()

    bonus_slider = Scale(root, from_=0.1, to=5, resolution=0.1, orient=HORIZONTAL, command=set_bonus)
    bonus_slider.set(1.15)
    bonus_slider.pack()

    noise_reduction_label = Label(root, text="Noise reduction")
    noise_reduction_label.pack()

    noise_reduction_slider = Scale(root, from_=0, to=100, resolution=0.1, orient=HORIZONTAL,
                                   command=set_noise_reduction)
    noise_reduction_slider.set(10)
    noise_reduction_slider.pack()

    # add button to turn off noise reduction
    def toggle_noise_reduction():
        global noise_reduction_active
        noise_reduction_active = not noise_reduction_active

    noise_reduction_button = Button(root, text="Toggle noise reduction", command=toggle_noise_reduction)
    noise_reduction_button.pack()

    root.mainloop()


x_len = 100  # Liczba punktów na osi x
y_range1 = [0, 10]  # Zakres osi y dla pierwszego wykresu
xdata1 = list(range(0, x_len))
ydata1 = [0] * x_len


def update_loudness_data(ydata, y_range):
    while (1):
        ydata.append(random.randint(y_range[0], y_range[1]))
        ydata.pop(0)
        time.sleep(0.01)


def update_loudness_plot(frame, ydata, line):
    line.set_ydata(ydata)
    return line,


fig1, ax1 = plt.subplots()
line1, = ax1.plot(xdata1, ydata1, lw=2)
ax1.set_ylim(y_range1)
ax1.set_xlim([0, x_len - 1])
ax1.set_xlabel('Czas')
ax1.set_ylabel('Wartość')
ax1.set_title('Wykres 1 w czasie rzeczywistym')

if __name__ == "__main__":
    audio = SharedAudioResource()

    with concurrent.futures.ThreadPoolExecutor(max_workers=7) as executor:
        future_pygame = executor.submit(pygame_thread, audio)

        future_sliders = executor.submit(tkinker_sliders)

        ani = animation.FuncAnimation(fig, update_plot, frames=100, blit=True)

        # TODO nie wiem czy to dziala na threadach
        # future_pygame.result()
        # future_sliders.result()
        executor.submit(future_pygame.result)
        executor.submit(future_sliders.result)

        executor.submit(update_loudness_data, ydata1, y_range1)
        ani1 = animation.FuncAnimation(fig1, update_loudness_plot, fargs=(ydata1, line1), interval=100, blit=True)

        plt.show()

    audio.close()
