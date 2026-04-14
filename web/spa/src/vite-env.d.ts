/// <reference types="vite/client" />

interface SpeechRecognition extends EventTarget {
  onresult: ((e: SpeechRecognitionEvent) => void) | null
  start(): void
}
