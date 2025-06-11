#include <WiFi.h>
#include <PubSubClient.h>
#include <DHT.h>
#include <time.h>
#include <Wire.h>
#include <BH1750.h>
#include <Timezone.h>

#define WIFI_SSID "A54 de Rui"
#define WIFI_PASSWORD "12345678"
#define MQTT_BROKER "192.168.63.110"
#define MQTT_PORT 1883
#define MQTT_TOPIC "sensor/0/data"          // Tópico correto para publicação
#define MQTT_CONTROL_TOPIC "sensor/0/control"  // Tópico correto para control

#define MQ135_PIN 34  // Pino do sensor MQ-135
#define BUZZER_PIN 27 // Pino do buzzer
#define CO2_ZERO 55  // Valor de referência do MQ-135 para calibração

BH1750 lightMeter;  // Instância do sensor de luminosidade
DHT dht(26, DHT11); // Instância do sensor dht11

const unsigned long Ta = 1000;  // Intervalo de amostragem em milissegundos
unsigned long Te;  // Intervalo de envio definido pelo utilizador
unsigned long lastSampleTime = 0;  // Armazena o tempo da última amostragem
unsigned long firstSampleTime = 0;  // Armazena o tempo da primeira amostra
bool firstSample = true;  // Flag para identificar a primeira amostra
unsigned long lastSendTime = 0;  // Armazena o tempo do último envio
bool running = false; // Variável para controlar o estado do programa

// Variáveis para armazenar os últimos valores lidos dos sensores
float lastTemp = 0.0;
float lastHumidity = 0.0;
float lastMQ135 = 0.0;
float lastLum = 0.0;  

WiFiClient espClient;
PubSubClient client(espClient);
// Definições para o fuso horário de Portugal
TimeChangeRule WET = { "WET", Last, Sun, Oct, 2, 0 };    // Horário de Inverno UTC+0
TimeChangeRule WEST = { "WEST", Last, Sun, Mar, 1, 60 }; // Horário de Verão UTC+1
Timezone portugalTime(WEST, WET);

void callback(char* topic, byte* payload, unsigned int length) {
    String message = "";
    for (int i = 0; i < length; i++) {
        message += (char)payload[i];
    }
    Serial.println("Recebido MQTT: " + message);

    // Split the message by '|'
    int firstDelimiter = message.indexOf('|');
    
    if (firstDelimiter > 0) {
        String command = message.substring(0, firstDelimiter);
        String id = message.substring(firstDelimiter + 1);
        
        // Processa comandos com dois elementos (comando stop)
        if (command == "1" && id == "0") {
            running = false;
            digitalWrite(BUZZER_PIN, LOW);
            noTone(BUZZER_PIN);  // Desliga qualquer tom que esteja tocando
            Serial.println("Finalizando coleta e envio de dados.");
            return;
        }
        
        // Processa comandos com três elementos (comando start)
        int secondDelimiter = message.indexOf('|', firstDelimiter + 1);
        if (secondDelimiter > firstDelimiter) {
            id = message.substring(firstDelimiter + 1, secondDelimiter);
            String param = message.substring(secondDelimiter + 1);
            
            if (id == "0" && command == "0") {
                int tempo = param.toInt() * 1000;
                if (tempo >= Ta) {
                    Te = tempo;
                    running = true;
                    lastSendTime = millis();
                    lastSampleTime = millis();
                    firstSample = true;
                    Serial.println("Iniciando coleta e envio de dados com intervalo: " + String(Te / 1000) + "s");
                } else {
                    Serial.println("Tempo de amostragem inválido, deve ser >= 1s");
                }
            }
        }
    }
}

void setup() {
    Serial.begin(115200); // Inicia a comunicação serial
    dht.begin();   // Inicia o sensor DHT11
    pinMode(BUZZER_PIN, OUTPUT); // Define o pino do buzzer como saída
    digitalWrite(BUZZER_PIN, LOW); // Mantém o buzzer desligado
    
    Wire.begin(33, 32); // Inicializa a comunicação I2C nos pinos 33 (SDA) e 32 (SCL)
    lightMeter.begin(BH1750::CONTINUOUS_HIGH_RES_MODE); // Inicializa o sensor BH1750

    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);  // Conecta ao WiFi
    Serial.print("Conectando ao WiFi");
    while (WiFi.status() != WL_CONNECTED) { // Aguarda conexão
        delay(500); 
        Serial.print(".");
    }
    Serial.println("\nWiFi conectado.");

    client.setServer(MQTT_BROKER, MQTT_PORT);
    client.setCallback(callback);
    connectMQTT();
    syncNTP();

    Serial.println("Sistema iniciado. Aguardando comando START via MQTT...");
    lastLum = lightMeter.readLightLevel(); // lê um primeiro valor para não considerar a luminosidade 0 no inicio
}

void loop() {
    if (!client.connected()) {
        connectMQTT();
    }
    client.loop();

    unsigned long currentTime = millis();

    // Always sample sensors every Ta interval (1 second), regardless of running state
    if (currentTime - lastSampleTime >= Ta) {
        lastSampleTime = currentTime;

        // Leitura dos sensores
        float temp = dht.readTemperature();
        float humidity = dht.readHumidity();
        float co2ppm = readMQ135();
        float lux = lightMeter.readLightLevel();

        // Armazena os valores lidos
        lastTemp = temp;
        lastHumidity = humidity;
        lastMQ135 = co2ppm;
        lastLum = lux;

        // Print to serial regardless of running state
        Serial.printf("Temperatura: %6.1f ºC     Humidade: %6.1f %%     MQ-135: %6.2f ppm     Luminosidade: %6.1f lux\n", 
                     temp, humidity, co2ppm, lux);
        
        // Verifica os alarmes
        checkAlarms(temp, humidity, co2ppm, lux);
    }

    // Only send data via MQTT if running is true
    if (running) {
        if (firstSample) {
            firstSampleTime = currentTime;
            lastSendTime = firstSampleTime;
            firstSample = false;
        }

        unsigned long relativeTime = currentTime - firstSampleTime; // Tempo relativo à primeira amostra

        // Envio dos dados no intervalo Te
        if (currentTime - lastSendTime >= Te) {
            lastSendTime = currentTime;
            
            // Formato para dados regulares: 0|id|timestamp|data
            String timestamp = getTimestamp();
            String sensorData = "temperature=" + String(lastTemp) + 
                               ",humidity=" + String(lastHumidity) + 
                               ",co2=" + String(lastMQ135) + 
                               ",luminosity=" + String(lastLum);
            
            String payload = "0|0|" + timestamp + "|" + sensorData;
            client.publish(MQTT_TOPIC, payload.c_str());
            
            Serial.printf("%s    Dados enviados para MQTT: %s\n", timestamp.c_str(), payload.c_str());
        }
    }
}

void sendAlarm(String id, float value, String type) {
    String timestamp = getTimestamp();
    String payload = "1|0|" + timestamp + "|" + String(value) + "|" + type;
    client.publish(MQTT_TOPIC, payload.c_str());
    Serial.println("Alarme enviado: " + payload);
}

void connectMQTT() {
    while (!client.connected()) {
        Serial.print("Conectando ao MQTT...");
        if (client.connect("ESP32Client")) {
            Serial.println("Conectado ao broker MQTT.");
            client.subscribe(MQTT_CONTROL_TOPIC);
        } else {
            Serial.print("Falha, rc=");
            Serial.print(client.state());
            Serial.println(" Tentando novamente em 5 segundos.");
            delay(5000);
        }
    }
}

float readMQ135() {
    return analogRead(MQ135_PIN) - CO2_ZERO;
}

void playAlarm(int alertCount) {
    if (alertCount == 0) {
        noTone(BUZZER_PIN);  // Sem alerta, buzzer desligado
    } else if (alertCount == 1) {
        tone(BUZZER_PIN, 1000);  // Som contínuo a 1 kHz para um único alerta
    } else if (alertCount == 2) {
        tone(BUZZER_PIN, 1500);  // Som contínuo a 1.5 kHz para dois alertas
    } else {
        tone(BUZZER_PIN, 2000);  // Som contínuo a 2 kHz para três ou mais alertas
    }
}

void checkAlarms(float temp, float humidity, float co2, float lux) {
    // Verificações de alarme
    bool tempAlert = temp > 27; // Verifica alarme de temperatura
    bool humidityAlert = humidity < 30; // Verifica alarme de humidade
    bool co2Alert = co2 > 1000;  // Verifica alarme de gases
    bool luxAlert = lux > 700; // Verifica alarme de luminosidade
    int alertCount = tempAlert + humidityAlert + co2Alert + luxAlert;

    if (tempAlert) sendAlarm("1", temp, "temperature");
    if (humidityAlert) sendAlarm("2", humidity, "humidity");
    if (co2Alert) sendAlarm("3", co2, "co2");
    if (luxAlert) sendAlarm("4", lux, "luminosity");

    
    // Ativar o alarme sonoro de acordo com o número de alertas
    playAlarm(alertCount);
}

void syncNTP() {
    Serial.println("Sincronizando com o servidor NTP...");
    configTime(0, 0, "pool.ntp.org", "time.nist.gov"); // Configure para UTC (offset 0)
    time_t now;
    do {
        delay(1000);
        now = time(nullptr);
    } while (now < 1000000000);
    Serial.println("Hora sincronizada com sucesso.");
}

String getTimestamp() {
    time_t utc = time(nullptr);
    time_t local = portugalTime.toLocal(utc);
    
    struct tm *timeinfo = localtime(&local);
    char buffer[64];
    strftime(buffer, sizeof(buffer), "%Y-%m-%d %H:%M:%S", timeinfo);
    return String(buffer);
}