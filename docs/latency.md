# Measuring latency

You can check for yourself how much delay Padthru adds to the picture, with a laptop, an HDMI cable and a phone camera.

1. Connect the laptop to the TV over HDMI and set Windows to **Duplicate** (Win + P), so the laptop screen and the TV show the same thing.
2. Open [`tools/lagtest.html`](../tools/lagtest.html) in a browser on the laptop and click it to go full screen. It shows a timer with milliseconds.
3. Take a few photos that capture the laptop screen and the TV together. In each photo, the difference between the two timers is how far the TV lags behind the laptop.
4. Do it twice: once with the TV on its normal HDMI input in Game Mode, and once inside Padthru with that input selected.

Compare the averages, not the absolute numbers. The absolute numbers include the laptop's own mirroring delay.

## Reference result

Samsung Q70A (2021), Game Mode on, three photos each:

| | Average lag behind the laptop |
|---|---|
| Normal HDMI input | ~67 ms |
| Inside Padthru | ~75 ms |

The difference is within one frame and within the noise of photographing two screens.

## Controller timing

The TV hands controller updates to apps about **42 times a second** (about 24 ms apart) on the tested model, even though Padthru reads them every 4 ms. On average that adds about 12 ms compared with a controller plugged into the PC. The network adds less than 1 ms on a wired home network.
