# xBAKE

A camera-based inspection tool that spots burnt, undercooked, or misshapen baked goods on a conveyor.

[Open the demo](https://kokutbon.github.io/xBAKE/) · [Technical documentation](docs/)

## Features

- Inspect products and track accepted and rejected items.
- Adjust image and classification settings in a web dashboard.
- Export inspection records and connect optional sorting hardware.

## Screenshots

![xBAKE main workspace](docs/images/dashboard.png)

![xBAKE detail view](docs/images/calibration.png)

## Technology

Python 3.10+, OpenCV, SQLite, browser dashboard; optional ONNX and hardware integrations.

## Run the demo locally

```sh
git clone https://github.com/KOKUTBON/xBAKE.git
cd xBAKE
python3 -m http.server 8000 --directory site
```

Open http://localhost:8000/. The browser demo uses sample data. See the technical documentation for the full application setup.

## License

[MIT License](LICENSE).
