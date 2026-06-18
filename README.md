# 🎵 IoT Music Player & Playback Analytics

![Raspberry Pi](https://img.shields.io/badge/-Raspberry_Pi_Zero_2W-C51A4A?style=for-the-badge&logo=Raspberry-Pi)
![MariaDB](https://img.shields.io/badge/MariaDB-003545?style=for-the-badge&logo=mariadb&logoColor=white)
![Node-RED](https://img.shields.io/badge/Node--RED-%238F0000.svg?style=for-the-badge&logo=node-red&logoColor=white)
![MQTT](https://img.shields.io/badge/MQTT-660066?style=for-the-badge&logo=mqtt&logoColor=white)
![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white)

Welcome to the **IoT Music Player** repository. This project is a complete hardware and software integration that transforms a Raspberry Pi into a high-fidelity network audio player with an advanced, automated playback tracking system.

## 📌 About the Project

This system is built around a **Raspberry Pi Zero 2W** paired with an **InnoMaker HiFi DAC Pro** to deliver high-resolution audio. Beyond just playing music from various sources (AirPlay, Spotify Connect, and local storage), this project implements a complete data pipeline to extract, transmit, and store playback metadata in real-time, allowing for deep analytics on personal listening habits.

## 🏗️ System Architecture

The project relies on a distributed, event-driven architecture to ensure no playback data is lost:

1. **Audio & Metadata Extraction (Python):** A custom Python script (`p.py`) monitors the audio daemon. Whenever a track changes, it extracts the metadata (Title, Artist, Album, Duration, and Source).
2. **Message Broker (MQTT):** The Python script publishes the JSON payload to a local Mosquitto MQTT broker via the `reproductor/metadata` topic.
3. **Middleware (Node-RED):** Node-RED subscribes to the MQTT topic, parses the JSON, and applies a 5-minute deduplication logic to prevent redundant database entries caused by network disconnects or retained messages.
4. **Data Storage (MariaDB):** Sanitized and validated data is securely inserted into a remote MariaDB server using parameterized SQL queries.

## ✨ Key Features

* **Multi-Source Support:** Seamlessly tracks music played via Spotify, Apple AirPlay, or local files.
* **High-Fidelity Audio:** Hardware-level integration with dedicated DAC modules.
* **Smart Deduplication:** Node-RED logic prevents database spam and ensures clean analytics.
* **Deep Data Analytics:** Includes complex SQL queries to generate "Spotify Wrapped" style summaries, highlighting daily obsessions, new discoveries, and top artists by source.

## 🗂️ Repository Structure

* `01_setup_mariadb.sql`: Database schema initialization, table creation with optimized indexes, and secure remote user provisioning.
* `ResumenMusical.sql`: A comprehensive suite of analytical SQL queries to extract listening habits and statistical summaries.
* `03_nodered_flow.json`: The Node-RED flow configuration for MQTT ingestion, payload deduplication, and database insertion.
* `04_implementation_guide.md`: Detailed step-by-step instructions for deploying the system, configuring network firewalls, and troubleshooting.

## 🚀 Getting Started

To replicate or deploy this system, please refer to the [Implementation Guide](https://github.com/Kylos02/MusicPlayer/blob/main/GuiaIntegraci%C3%B3nMariaDB/README.md) included in this repository. It covers everything from setting up MariaDB on a local machine to importing the Node-RED flow on the Raspberry Pi.

---
*Designed and developed as an exploration of IoT architecture, embedded systems, and database management.*
