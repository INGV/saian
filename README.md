
# AI-Assisted Seismic Phase Picking via Visual Waveform Interpretation

## Overview

This project provides a Python tool designed to support a workflow where a generative AI specialized in seismic monitoring analyzes standardized waveform images to detect seismic signals and estimate seismic phase arrivals.

The core idea is to reproduce the visual workflow of an experienced seismic analyst in a seismic monitoring room, allowing the AI to interpret waveform plots rather than raw time series data.

The tool performs waveform acquisition, standardized plotting, and automated zoom generation around candidate seismic phases to enable iterative AI-assisted phase picking.

---

# Workflow

The workflow is structured in two main stages.

## 1. Signal Detection and Preliminary Picking

The tool generates a full waveform plot from seismic data retrieved via FDSN services.

The generative AI receives this image and determines:

- whether a seismic signal is present  
- whether a P phase is visible  
- whether a S phase is visible    
- approximate positions of the phases on the waveform     

The AI returns approximate pick positions measured directly on the full waveform image.

---

## 2. High-Resolution Phase Refinement

Using the preliminary picks returned by the AI, the tool automatically generates high-resolution zoom plots centered on:

- the P phase  
- the S phase  

These zoomed images are then provided back to the AI for fine picking.

At this stage the AI returns:

- refined P pick  
- refined S pick  
- phase visibility (if the phase is not observable)  
- uncertainty estimate (possibly asymmetric)  
- polarity, when determinable

---

# Key Design Principles

The project is based on several principles:

### Visual interpretation instead of raw waveform input

Instead of feeding raw waveform arrays directly into a model, the system presents carefully designed waveform plots that mimic the visual representation used by human analysts.

### Standardized plotting

Plots are generated with controlled properties:

- high resolution  
- consistent time axes  
- precise tick spacing  
- standardized scaling

This ensures both human readability and AI interpretability.

### Iterative refinement

The picking process is intentionally split into two stages:

1. coarse detection on the full waveform  
2. high-precision picking on zoomed views  

This approach mirrors the workflow used in manual seismic analysis.

---

# Features

The current tool provides:

- waveform download via FDSN dataselect  
- metadata caching via FDSN station  
- per-channel MiniSEED export  
- standardized waveform plots  
- automatic P and S zoom generation  
- configurable plotting parameters via JSON