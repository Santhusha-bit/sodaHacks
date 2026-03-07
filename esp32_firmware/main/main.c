/*
 * main.c — ESP32-S3-BOX-3 Firmware for Blind Pedestrian Navigation
 *
 * Built for ESP-IDF (NOT Arduino). Flash using the ESP-IDF VS Code extension
 * or via idf.py from the ESP-IDF command line:
 *
 *   idf.py set-target esp32s3
 *   idf.py build flash monitor
 *
 * Hardware:
 *   GPIO 4  → Piezoelectric haptic disc (via transistor/MOSFET driver)
 *   GPIO 5  → Distance sensor trigger (HC-SR04 or JSN-SR04T)
 *   GPIO 6  → Distance sensor echo
 *   Built-in microphone, speaker, and 2.4" LCD on ESP32-S3-BOX-3
 *
 * Serial Protocol (USB-C @ 115200 baud, JSON newline-delimited):
 *
 *   ESP32 → Python:
 *     {"event":"distance","cm":45.3,"uptime_ms":12345}
 *     {"event":"heartbeat","uptime_ms":30000}
 *
 *   Python → ESP32:
 *     {"haptic":"pulse"}   — single buzz (CAUTION)
 *     {"haptic":"rapid"}   — rapid bursts (DANGER)
 *     {"haptic":"long"}    — long 1s buzz (SAFE/walk signal)
 *     {"haptic":"calm"}    — soft tap (all clear)
 *     {"alert":"Car 3 feet ahead. Stop."}  — text to speak on built-in speaker
 *     {"display":{"line1":"DANGER","line2":"Car ahead"}}
 */

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/gpio.h"
#include "driver/ledc.h"
#include "esp_log.h"
#include "esp_timer.h"

static const char *TAG = "PedestrianAgent";

/* ── GPIO Pin Definitions ────────────────────────────────────────────────── */
#define PIN_PIEZO           GPIO_NUM_4
#define PIN_DIST_TRIGGER    GPIO_NUM_5
#define PIN_DIST_ECHO       GPIO_NUM_6

/* ── Constants ───────────────────────────────────────────────────────────── */
#define SOUND_CM_PER_US     0.0343f   /* speed of sound: 343m/s → 0.0343 cm/µs */
#define MAX_DIST_CM         400.0f    /* max reliable measurement */
#define DIST_READ_INTERVAL_MS 200     /* read every 200ms */
#define SERIAL_BUF_SIZE     256

/* ── Haptic Patterns ─────────────────────────────────────────────────────── */
typedef struct { uint32_t on_ms; uint32_t off_ms; int reps; } HapticPattern;

/* Pattern definitions — tune frequency for sensation */
#define HAPTIC_FREQ_HZ      250   /* 250Hz gives a clear palpable buzz */

static void haptic_buzz(uint32_t duration_ms)
{
    ledc_set_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0, 4096); /* 50% duty */
    ledc_update_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0);
    vTaskDelay(pdMS_TO_TICKS(duration_ms));
    ledc_set_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0, 0);
    ledc_update_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0);
}

static void haptic_play(const char *pattern_name)
{
    if (strcmp(pattern_name, "calm") == 0) {
        haptic_buzz(80);

    } else if (strcmp(pattern_name, "pulse") == 0) {
        haptic_buzz(300);

    } else if (strcmp(pattern_name, "double") == 0) {
        haptic_buzz(150); vTaskDelay(pdMS_TO_TICKS(80)); haptic_buzz(150);

    } else if (strcmp(pattern_name, "rapid") == 0) {
        /* Three rapid 80ms bursts — DANGER */
        for (int i = 0; i < 3; i++) {
            haptic_buzz(80);
            vTaskDelay(pdMS_TO_TICKS(60));
        }

    } else if (strcmp(pattern_name, "long") == 0) {
        /* 1s buzz = walk signal is ON — safe to cross */
        haptic_buzz(1000);

    } else {
        haptic_buzz(200); /* default */
    }
}

/* ── Distance Sensor (HC-SR04) ───────────────────────────────────────────── */
static float read_distance_cm(void)
{
    /* Send 10µs trigger pulse */
    gpio_set_level(PIN_DIST_TRIGGER, 0);
    esp_rom_delay_us(2);
    gpio_set_level(PIN_DIST_TRIGGER, 1);
    esp_rom_delay_us(10);
    gpio_set_level(PIN_DIST_TRIGGER, 0);

    /* Wait for echo to go HIGH */
    int64_t start = esp_timer_get_time();
    while (gpio_get_level(PIN_DIST_ECHO) == 0) {
        if ((esp_timer_get_time() - start) > 30000) return -1.0f; /* timeout */
    }

    /* Measure echo pulse width */
    int64_t echo_start = esp_timer_get_time();
    while (gpio_get_level(PIN_DIST_ECHO) == 1) {
        if ((esp_timer_get_time() - echo_start) > 30000) return -1.0f;
    }
    int64_t echo_end = esp_timer_get_time();

    float duration_us = (float)(echo_end - echo_start);
    float dist_cm = (duration_us * SOUND_CM_PER_US) / 2.0f;

    return (dist_cm > MAX_DIST_CM) ? -1.0f : dist_cm;
}

/* ── Serial Command Parser ───────────────────────────────────────────────── */
static void parse_command(const char *cmd)
{
    /*
     * Minimal JSON parser — avoids pulling in a full JSON library.
     * Looks for key substrings: "haptic" and "alert".
     */

    /* Haptic command: {"haptic":"rapid"} */
    char *haptic_key = strstr(cmd, "\"haptic\"");
    if (haptic_key) {
        char *val_start = strchr(haptic_key, ':');
        if (val_start) {
            val_start = strchr(val_start, '"');
            if (val_start) {
                val_start++; /* skip opening quote */
                char *val_end = strchr(val_start, '"');
                if (val_end) {
                    char pattern[32] = {0};
                    size_t len = (size_t)(val_end - val_start);
                    if (len < sizeof(pattern)) {
                        strncpy(pattern, val_start, len);
                        ESP_LOGI(TAG, "Haptic: %s", pattern);
                        haptic_play(pattern);
                    }
                }
            }
        }
        return;
    }

    /* Alert text command: {"alert":"Car 3 feet ahead"} */
    char *alert_key = strstr(cmd, "\"alert\"");
    if (alert_key) {
        char *val_start = strchr(alert_key, ':');
        if (val_start) {
            val_start = strchr(val_start, '"');
            if (val_start) {
                val_start++;
                char *val_end = strchr(val_start, '"');
                if (val_end) {
                    char alert_text[128] = {0};
                    size_t len = (size_t)(val_end - val_start);
                    if (len < sizeof(alert_text)) {
                        strncpy(alert_text, val_start, len);
                        /* TODO: Feed to ESP32-S3-BOX built-in TTS / speaker */
                        ESP_LOGI(TAG, "Alert: %s", alert_text);
                    }
                }
            }
        }
    }
}

/* ── Distance Sensor Task ────────────────────────────────────────────────── */
static void distance_task(void *pvParameters)
{
    while (1) {
        float dist = read_distance_cm();

        if (dist > 0) {
            /* Emit JSON sensor reading over UART (USB serial) */
            printf("{\"event\":\"distance\",\"cm\":%.1f,\"uptime_ms\":%lld}\n",
                   dist, esp_timer_get_time() / 1000);
        }

        vTaskDelay(pdMS_TO_TICKS(DIST_READ_INTERVAL_MS));
    }
}

/* ── Serial Reader Task ──────────────────────────────────────────────────── */
static void serial_reader_task(void *pvParameters)
{
    char buf[SERIAL_BUF_SIZE];
    int  pos = 0;

    while (1) {
        int ch = fgetc(stdin);
        if (ch == EOF) {
            vTaskDelay(pdMS_TO_TICKS(10));
            continue;
        }
        if (ch == '\n' || ch == '\r') {
            if (pos > 0) {
                buf[pos] = '\0';
                parse_command(buf);
                pos = 0;
            }
        } else if (pos < SERIAL_BUF_SIZE - 1) {
            buf[pos++] = (char)ch;
        }
    }
}

/* ── Heartbeat Task ──────────────────────────────────────────────────────── */
static void heartbeat_task(void *pvParameters)
{
    while (1) {
        vTaskDelay(pdMS_TO_TICKS(30000)); /* every 30s */
        printf("{\"event\":\"heartbeat\",\"uptime_ms\":%lld}\n",
               esp_timer_get_time() / 1000);
    }
}

/* ── GPIO & LEDC Init ────────────────────────────────────────────────────── */
static void hardware_init(void)
{
    /* Piezo via LEDC (PWM) */
    ledc_timer_config_t timer = {
        .speed_mode      = LEDC_LOW_SPEED_MODE,
        .timer_num       = LEDC_TIMER_0,
        .duty_resolution = LEDC_TIMER_13_BIT,
        .freq_hz         = HAPTIC_FREQ_HZ,
        .clk_cfg         = LEDC_AUTO_CLK,
    };
    ledc_timer_config(&timer);

    ledc_channel_config_t channel = {
        .gpio_num   = PIN_PIEZO,
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .channel    = LEDC_CHANNEL_0,
        .timer_sel  = LEDC_TIMER_0,
        .duty       = 0,
        .hpoint     = 0,
    };
    ledc_channel_config(&channel);

    /* Distance sensor pins */
    gpio_set_direction(PIN_DIST_TRIGGER, GPIO_MODE_OUTPUT);
    gpio_set_direction(PIN_DIST_ECHO,    GPIO_MODE_INPUT);
    gpio_set_level(PIN_DIST_TRIGGER, 0);
}

/* ── Entry Point ─────────────────────────────────────────────────────────── */
void app_main(void)
{
    ESP_LOGI(TAG, "Blind Pedestrian Navigation Agent — ESP-IDF v5.x");

    hardware_init();

    /* Boot confirmation — two haptic taps */
    haptic_buzz(100); vTaskDelay(pdMS_TO_TICKS(80)); haptic_buzz(100);

    printf("{\"event\":\"boot\",\"msg\":\"Pedestrian navigation agent online\"}\n");

    /* Spawn FreeRTOS tasks */
    xTaskCreate(distance_task,    "dist",  4096, NULL, 5, NULL);
    xTaskCreate(serial_reader_task, "ser", 4096, NULL, 4, NULL);
    xTaskCreate(heartbeat_task,   "hb",   2048, NULL, 1, NULL);
}
