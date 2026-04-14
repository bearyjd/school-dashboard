/// <reference types="vite/client" />

interface SpeechRecognitionEvent extends Event {
  results: SpeechRecognitionResultList
}

interface SpeechRecognition extends EventTarget {
  onresult: ((e: SpeechRecognitionEvent) => void) | null
  start(): void
}
