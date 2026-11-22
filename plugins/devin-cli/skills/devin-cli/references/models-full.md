# devin models 原文（2026-11-21 擷取；不含 995 個 Fusion 組合）

重抓：`devin models`。Fusion 組合代號格式：`fusion-<主模型代號>-sidekick-<swe-2-medium|swe-2-high|swe-2-flash-max>`。

```
Available models (55 families)

Adaptive (adaptive)
  adaptive                                                                   Adaptive  [$0.5 / 1M Input · $0.1 / 1M Cached input · $2 / 1M Output]

SWE-2 (swe-2)
  aliases: swe
  swe-2-high                                                                 SWE-2 High  [262K context, Free]
  swe-2-medium                                                               SWE-2 Medium  [262K context, Free]
  swe-2-max                                                                  SWE-2 Max  [262K context, Free]
  swe-2-medium-lightning                                                     SWE-2 Medium Lightning  [262K context, $1 / 1M Input · $0.1 / 1M Cached input · $5 / 1M Output]
  swe-2-high-lightning                                                       SWE-2 High Lightning  [262K context, $1 / 1M Input · $0.1 / 1M Cached input · $5 / 1M Output]
  swe-2-max-lightning                                                        SWE-2 Max Lightning  [262K context, $1 / 1M Input · $0.1 / 1M Cached input · $5 / 1M Output]

SWE-2 Flash (swe-2-flash)
  swe-2-flash-medium                                                         SWE-2 Flash Medium  [262K context, Free]
  swe-2-flash-high                                                           SWE-2 Flash High  [262K context, Free]
  swe-2-flash-max                                                            SWE-2 Flash Max  [262K context, Free]
  swe-2-flash-medium-lightning                                               SWE-2 Flash Medium Lightning  [262K context, $1 / 1M Input · $0.1 / 1M Cached input · $5 / 1M Output, new]
  swe-2-flash-high-lightning                                                 SWE-2 Flash High Lightning  [262K context, $1 / 1M Input · $0.1 / 1M Cached input · $5 / 1M Output, new]
  swe-2-flash-max-lightning                                                  SWE-2 Flash Max Lightning  [262K context, $1 / 1M Input · $0.1 / 1M Cached input · $5 / 1M Output, new]

Claude Fable 5.2 (claude-fable-5.2)
  claude-fable-5-2-medium                                                    Claude Fable 5.2 Medium  [1M context, $8 / 1M Input · $0.2 / 1M Cached input · $40 / 1M Output]
  claude-fable-5-2-low                                                       Claude Fable 5.2 Low  [1M context, $8 / 1M Input · $0.2 / 1M Cached input · $40 / 1M Output]
  claude-fable-5-2-high                                                      Claude Fable 5.2 High  [1M context, $8 / 1M Input · $0.2 / 1M Cached input · $40 / 1M Output]
  claude-fable-5-2-xhigh                                                     Claude Fable 5.2 XHigh  [1M context, $8 / 1M Input · $0.2 / 1M Cached input · $40 / 1M Output]
  claude-fable-5-2-max                                                       Claude Fable 5.2 Max  [1M context, $8 / 1M Input · $0.2 / 1M Cached input · $40 / 1M Output]
  claude-fable-5-2-low-fast                                                  Claude Fable 5.2 Low Fast  [1M context, $16 / 1M Input · $0.4 / 1M Cached input · $80 / 1M Output]
  claude-fable-5-2-medium-fast                                               Claude Fable 5.2 Medium Fast  [1M context, $16 / 1M Input · $0.4 / 1M Cached input · $80 / 1M Output]
  claude-fable-5-2-high-fast                                                 Claude Fable 5.2 High Fast  [1M context, $16 / 1M Input · $0.4 / 1M Cached input · $80 / 1M Output]
  claude-fable-5-2-xhigh-fast                                                Claude Fable 5.2 XHigh Fast  [1M context, $16 / 1M Input · $0.4 / 1M Cached input · $80 / 1M Output]
  claude-fable-5-2-max-fast                                                  Claude Fable 5.2 Max Fast  [1M context, $16 / 1M Input · $0.4 / 1M Cached input · $80 / 1M Output]

Claude Opus 5.5 (claude-opus-5.5)
  claude-opus-5-5-medium                                                     Claude Opus 5.5 Medium  [1M context, $4 / 1M Input · $0.2 / 1M Cached input · $20 / 1M Output]
  claude-opus-5-5-low                                                        Claude Opus 5.5 Low  [1M context, $4 / 1M Input · $0.2 / 1M Cached input · $20 / 1M Output]
  claude-opus-5-5-high                                                       Claude Opus 5.5 High  [1M context, $4 / 1M Input · $0.2 / 1M Cached input · $20 / 1M Output]
  claude-opus-5-5-xhigh                                                      Claude Opus 5.5 XHigh  [1M context, $4 / 1M Input · $0.2 / 1M Cached input · $20 / 1M Output]
  claude-opus-5-5-max                                                        Claude Opus 5.5 Max  [1M context, $4 / 1M Input · $0.2 / 1M Cached input · $20 / 1M Output]
  claude-opus-5-5-low-fast                                                   Claude Opus 5.5 Low Fast  [1M context, $8 / 1M Input · $0.4 / 1M Cached input · $40 / 1M Output]
  claude-opus-5-5-medium-fast                                                Claude Opus 5.5 Medium Fast  [1M context, $8 / 1M Input · $0.4 / 1M Cached input · $40 / 1M Output]
  claude-opus-5-5-high-fast                                                  Claude Opus 5.5 High Fast  [1M context, $8 / 1M Input · $0.4 / 1M Cached input · $40 / 1M Output]
  claude-opus-5-5-xhigh-fast                                                 Claude Opus 5.5 XHigh Fast  [1M context, $8 / 1M Input · $0.4 / 1M Cached input · $40 / 1M Output]
  claude-opus-5-5-max-fast                                                   Claude Opus 5.5 Max Fast  [1M context, $8 / 1M Input · $0.4 / 1M Cached input · $40 / 1M Output]

GPT-6.2 Astra (gpt-6.2-astra)
  gpt-6-2-astra-medium                                                       GPT-6.2 Astra Medium Thinking  [1.05M context, $8 / 1M Input · $0.4 / 1M Cached input · $40 / 1M Output, new]
  gpt-6-2-astra-low                                                          GPT-6.2 Astra Low Thinking  [1.05M context, $8 / 1M Input · $0.4 / 1M Cached input · $40 / 1M Output, new]
  gpt-6-2-astra-high                                                         GPT-6.2 Astra High Thinking  [1.05M context, $8 / 1M Input · $0.4 / 1M Cached input · $40 / 1M Output, new]
  gpt-6-2-astra-xhigh                                                        GPT-6.2 Astra XHigh Thinking  [1.05M context, $8 / 1M Input · $0.4 / 1M Cached input · $40 / 1M Output, new]
  gpt-6-2-astra-max                                                          GPT-6.2 Astra Max Thinking  [1.05M context, $8 / 1M Input · $0.4 / 1M Cached input · $40 / 1M Output, new]
  gpt-6-2-astra-low-priority                                                 GPT-6.2 Astra Low Thinking Fast  [1.05M context, $16 / 1M Input · $0.8 / 1M Cached input · $80 / 1M Output, new]
  gpt-6-2-astra-medium-priority                                              GPT-6.2 Astra Medium Thinking Fast  [1.05M context, $16 / 1M Input · $0.8 / 1M Cached input · $80 / 1M Output, new]
  gpt-6-2-astra-high-priority                                                GPT-6.2 Astra High Thinking Fast  [1.05M context, $16 / 1M Input · $0.8 / 1M Cached input · $80 / 1M Output, new]
  gpt-6-2-astra-xhigh-priority                                               GPT-6.2 Astra XHigh Thinking Fast  [1.05M context, $16 / 1M Input · $0.8 / 1M Cached input · $80 / 1M Output, new]
  gpt-6-2-astra-max-priority                                                 GPT-6.2 Astra Max Thinking Fast  [1.05M context, $16 / 1M Input · $0.8 / 1M Cached input · $80 / 1M Output, new]

GPT-6 Astra (gpt-6-astra)
  gpt-6-astra-medium                                                         GPT-6 Astra Medium Thinking  [1M context, $10 / 1M Input · $1 / 1M Cached input · $50 / 1M Output]
  gpt-6-astra-low                                                            GPT-6 Astra Low Thinking  [1M context, $10 / 1M Input · $1 / 1M Cached input · $50 / 1M Output]
  gpt-6-astra-high                                                           GPT-6 Astra High Thinking  [1M context, $10 / 1M Input · $1 / 1M Cached input · $50 / 1M Output]
  gpt-6-astra-xhigh                                                          GPT-6 Astra XHigh Thinking  [1M context, $10 / 1M Input · $1 / 1M Cached input · $50 / 1M Output]
  gpt-6-astra-max                                                            GPT-6 Astra Max Thinking  [1M context, $10 / 1M Input · $1 / 1M Cached input · $50 / 1M Output]
  gpt-6-astra-low-priority                                                   GPT-6 Astra Low Thinking Fast  [1M context, $20 / 1M Input · $2 / 1M Cached input · $100 / 1M Output]
  gpt-6-astra-medium-priority                                                GPT-6 Astra Medium Thinking Fast  [1M context, $20 / 1M Input · $2 / 1M Cached input · $100 / 1M Output]
  gpt-6-astra-high-priority                                                  GPT-6 Astra High Thinking Fast  [1M context, $20 / 1M Input · $2 / 1M Cached input · $100 / 1M Output]
  gpt-6-astra-xhigh-priority                                                 GPT-6 Astra XHigh Thinking Fast  [1M context, $20 / 1M Input · $2 / 1M Cached input · $100 / 1M Output]
  gpt-6-astra-max-priority                                                   GPT-6 Astra Max Thinking Fast  [1M context, $20 / 1M Input · $2 / 1M Cached input · $100 / 1M Output]

GPT-6 Sol (gpt-6-sol)
  gpt-6-sol-medium                                                           GPT-6 Sol Medium Thinking  [1M context, $2 / 1M Input · $0.2 / 1M Cached input · $10 / 1M Output]
  gpt-6-sol-none                                                             GPT-6 Sol No Thinking  [1M context, $2 / 1M Input · $0.2 / 1M Cached input · $10 / 1M Output]
  gpt-6-sol-low                                                              GPT-6 Sol Low Thinking  [1M context, $2 / 1M Input · $0.2 / 1M Cached input · $10 / 1M Output]
  gpt-6-sol-high                                                             GPT-6 Sol High Thinking  [1M context, $2 / 1M Input · $0.2 / 1M Cached input · $10 / 1M Output]
  gpt-6-sol-xhigh                                                            GPT-6 Sol XHigh Thinking  [1M context, $2 / 1M Input · $0.2 / 1M Cached input · $10 / 1M Output]
  gpt-6-sol-max                                                              GPT-6 Sol Max Thinking  [1M context, $2 / 1M Input · $0.2 / 1M Cached input · $10 / 1M Output]
  gpt-6-sol-none-priority                                                    GPT-6 Sol No Thinking Fast  [1M context, $4 / 1M Input · $0.4 / 1M Cached input · $20 / 1M Output]
  gpt-6-sol-low-priority                                                     GPT-6 Sol Low Thinking Fast  [1M context, $4 / 1M Input · $0.4 / 1M Cached input · $20 / 1M Output]
  gpt-6-sol-medium-priority                                                  GPT-6 Sol Medium Thinking Fast  [1M context, $4 / 1M Input · $0.4 / 1M Cached input · $20 / 1M Output]
  gpt-6-sol-high-priority                                                    GPT-6 Sol High Thinking Fast  [1M context, $4 / 1M Input · $0.4 / 1M Cached input · $20 / 1M Output]
  gpt-6-sol-xhigh-priority                                                   GPT-6 Sol XHigh Thinking Fast  [1M context, $4 / 1M Input · $0.4 / 1M Cached input · $20 / 1M Output]
  gpt-6-sol-max-priority                                                     GPT-6 Sol Max Thinking Fast  [1M context, $4 / 1M Input · $0.4 / 1M Cached input · $20 / 1M Output]

GPT-6 Luna (gpt-6-luna)
  gpt-6-luna-medium                                                          GPT-6 Luna Medium Thinking  [1M context, $0.1 / 1M Input · $0.01 / 1M Cached input · $0.5 / 1M Output]
  gpt-6-luna-none                                                            GPT-6 Luna No Thinking  [1M context, $0.1 / 1M Input · $0.01 / 1M Cached input · $0.5 / 1M Output]
  gpt-6-luna-low                                                             GPT-6 Luna Low Thinking  [1M context, $0.1 / 1M Input · $0.01 / 1M Cached input · $0.5 / 1M Output]
  gpt-6-luna-high                                                            GPT-6 Luna High Thinking  [1M context, $0.1 / 1M Input · $0.01 / 1M Cached input · $0.5 / 1M Output]
  gpt-6-luna-xhigh                                                           GPT-6 Luna XHigh Thinking  [1M context, $0.1 / 1M Input · $0.01 / 1M Cached input · $0.5 / 1M Output]
  gpt-6-luna-max                                                             GPT-6 Luna Max Thinking  [1M context, $0.1 / 1M Input · $0.01 / 1M Cached input · $0.5 / 1M Output]
  gpt-6-luna-none-priority                                                   GPT-6 Luna No Thinking Fast  [1M context, $0.2 / 1M Input · $0.02 / 1M Cached input · $1 / 1M Output]
  gpt-6-luna-low-priority                                                    GPT-6 Luna Low Thinking Fast  [1M context, $0.2 / 1M Input · $0.02 / 1M Cached input · $1 / 1M Output]
  gpt-6-luna-medium-priority                                                 GPT-6 Luna Medium Thinking Fast  [1M context, $0.2 / 1M Input · $0.02 / 1M Cached input · $1 / 1M Output]
  gpt-6-luna-high-priority                                                   GPT-6 Luna High Thinking Fast  [1M context, $0.2 / 1M Input · $0.02 / 1M Cached input · $1 / 1M Output]
  gpt-6-luna-xhigh-priority                                                  GPT-6 Luna XHigh Thinking Fast  [1M context, $0.2 / 1M Input · $0.02 / 1M Cached input · $1 / 1M Output]
  gpt-6-luna-max-priority                                                    GPT-6 Luna Max Thinking Fast  [1M context, $0.2 / 1M Input · $0.02 / 1M Cached input · $1 / 1M Output]

Gemini 4 Carbon (gemini-4-carbon)
  gemini-4-carbon-medium                                                     Gemini 4 Carbon Medium  [1.05M context, $2 / 1M Input · $0.1 / 1M Cached input · $10 / 1M Output]
  gemini-4-carbon-low                                                        Gemini 4 Carbon Low  [1.05M context, $2 / 1M Input · $0.1 / 1M Cached input · $10 / 1M Output]
  gemini-4-carbon-high                                                       Gemini 4 Carbon High  [1.05M context, $2 / 1M Input · $0.1 / 1M Cached input · $10 / 1M Output]

Kimi K3.1 Pro (kimi-k3.1-pro)
  kimi-k3-1-pro-max                                                          Kimi K3.1 Pro Max  [1.05M context, $2 / 1M Input · $0.2 / 1M Cached input · $10 / 1M Output]
  kimi-k3-1-pro-low                                                          Kimi K3.1 Pro Low  [1.05M context, $2 / 1M Input · $0.2 / 1M Cached input · $10 / 1M Output]
  kimi-k3-1-pro-medium                                                       Kimi K3.1 Pro Medium  [1.05M context, $2 / 1M Input · $0.2 / 1M Cached input · $10 / 1M Output]
  kimi-k3-1-pro-high                                                         Kimi K3.1 Pro High  [1.05M context, $2 / 1M Input · $0.2 / 1M Cached input · $10 / 1M Output]

GLM-5.3 (glm-5.3)
  glm-5-3-low                                                                GLM-5.3 Low  [1.05M context, $1.4 / 1M Input · $0.26 / 1M Cached input · $4.4 / 1M Output]
  glm-5-3-high                                                               GLM-5.3 High  [1.05M context, $1.4 / 1M Input · $0.26 / 1M Cached input · $4.4 / 1M Output]
  glm-5-3-max                                                                GLM-5.3 Max  [1.05M context, $1.4 / 1M Input · $0.26 / 1M Cached input · $4.4 / 1M Output]

Claude Sonnet 5.5 (claude-sonnet-5.5)
  claude-sonnet-5-5-medium                                                   Claude Sonnet 5.5 Medium  [1M context, $2 / 1M Input · $0.1 / 1M Cached input · $10 / 1M Output]
  claude-sonnet-5-5-low                                                      Claude Sonnet 5.5 Low  [1M context, $2 / 1M Input · $0.1 / 1M Cached input · $10 / 1M Output]
  claude-sonnet-5-5-high                                                     Claude Sonnet 5.5 High  [1M context, $2 / 1M Input · $0.1 / 1M Cached input · $10 / 1M Output]
  claude-sonnet-5-5-xhigh                                                    Claude Sonnet 5.5 XHigh  [1M context, $2 / 1M Input · $0.1 / 1M Cached input · $10 / 1M Output]
  claude-sonnet-5-5-max                                                      Claude Sonnet 5.5 Max  [1M context, $2 / 1M Input · $0.1 / 1M Cached input · $10 / 1M Output]

Gemini 3.8 Flash (gemini-3.8-flash)
  aliases: gemini
  gemini-3-8-flash-medium                                                    Gemini 3.8 Flash Medium  [1.05M context, $0.75 / 1M Input · $0.08 / 1M Cached input · $3.75 / 1M Output]
  gemini-3-8-flash-low                                                       Gemini 3.8 Flash Low  [1.05M context, $0.75 / 1M Input · $0.08 / 1M Cached input · $3.75 / 1M Output]
  gemini-3-8-flash-high                                                      Gemini 3.8 Flash High  [1.05M context, $0.75 / 1M Input · $0.08 / 1M Cached input · $3.75 / 1M Output]

Claude Opus 4.7 (claude-opus-4.7)
  claude-opus-4-7-medium                                                     Claude Opus 4.7 Medium  [1M context, $5 / 1M Input · $0.5 / 1M Cached input · $25 / 1M Output]
  claude-opus-4-7-low                                                        Claude Opus 4.7 Low  [1M context, $5 / 1M Input · $0.5 / 1M Cached input · $25 / 1M Output]
  claude-opus-4-7-high                                                       Claude Opus 4.7 High  [1M context, $5 / 1M Input · $0.5 / 1M Cached input · $25 / 1M Output]
  claude-opus-4-7-xhigh                                                      Claude Opus 4.7 XHigh  [1M context, $5 / 1M Input · $0.5 / 1M Cached input · $25 / 1M Output]
  claude-opus-4-7-max                                                        Claude Opus 4.7 Max  [1M context, $5 / 1M Input · $0.5 / 1M Cached input · $25 / 1M Output]

Claude Opus 4.8 (claude-opus-4.8)
  claude-opus-4-8-medium                                                     Claude Opus 4.8 Medium  [1M context, $5 / 1M Input · $0.5 / 1M Cached input · $25 / 1M Output]
  claude-opus-4-8-low                                                        Claude Opus 4.8 Low  [1M context, $5 / 1M Input · $0.5 / 1M Cached input · $25 / 1M Output]
  claude-opus-4-8-high                                                       Claude Opus 4.8 High  [1M context, $5 / 1M Input · $0.5 / 1M Cached input · $25 / 1M Output]
  claude-opus-4-8-xhigh                                                      Claude Opus 4.8 XHigh  [1M context, $5 / 1M Input · $0.5 / 1M Cached input · $25 / 1M Output]
  claude-opus-4-8-max                                                        Claude Opus 4.8 Max  [1M context, $5 / 1M Input · $0.5 / 1M Cached input · $25 / 1M Output]
  claude-opus-4-8-low-fast                                                   Claude Opus 4.8 Low Fast  [1M context, $10 / 1M Input · $1 / 1M Cached input · $50 / 1M Output]
  claude-opus-4-8-medium-fast                                                Claude Opus 4.8 Medium Fast  [1M context, $10 / 1M Input · $1 / 1M Cached input · $50 / 1M Output]
  claude-opus-4-8-high-fast                                                  Claude Opus 4.8 High Fast  [1M context, $10 / 1M Input · $1 / 1M Cached input · $50 / 1M Output]
  claude-opus-4-8-xhigh-fast                                                 Claude Opus 4.8 XHigh Fast  [1M context, $10 / 1M Input · $1 / 1M Cached input · $50 / 1M Output]
  claude-opus-4-8-max-fast                                                   Claude Opus 4.8 Max Fast  [1M context, $10 / 1M Input · $1 / 1M Cached input · $50 / 1M Output]

Claude Opus 5 (claude-opus-5)
  aliases: opus
  claude-opus-5-medium                                                       Claude Opus 5 Medium  [1M context, $5 / 1M Input · $0.5 / 1M Cached input · $25 / 1M Output]
  claude-opus-5-low                                                          Claude Opus 5 Low  [1M context, $5 / 1M Input · $0.5 / 1M Cached input · $25 / 1M Output]
  claude-opus-5-high                                                         Claude Opus 5 High  [1M context, $5 / 1M Input · $0.5 / 1M Cached input · $25 / 1M Output]
  claude-opus-5-xhigh                                                        Claude Opus 5 XHigh  [1M context, $5 / 1M Input · $0.5 / 1M Cached input · $25 / 1M Output]
  claude-opus-5-max                                                          Claude Opus 5 Max  [1M context, $5 / 1M Input · $0.5 / 1M Cached input · $25 / 1M Output]
  claude-opus-5-low-fast                                                     Claude Opus 5 Low Fast  [1M context, $10 / 1M Input · $1 / 1M Cached input · $50 / 1M Output]
  claude-opus-5-medium-fast                                                  Claude Opus 5 Medium Fast  [1M context, $10 / 1M Input · $1 / 1M Cached input · $50 / 1M Output]
  claude-opus-5-high-fast                                                    Claude Opus 5 High Fast  [1M context, $10 / 1M Input · $1 / 1M Cached input · $50 / 1M Output]
  claude-opus-5-xhigh-fast                                                   Claude Opus 5 XHigh Fast  [1M context, $10 / 1M Input · $1 / 1M Cached input · $50 / 1M Output]
  claude-opus-5-max-fast                                                     Claude Opus 5 Max Fast  [1M context, $10 / 1M Input · $1 / 1M Cached input · $50 / 1M Output]

Claude Fable 5 (claude-fable-5)
  claude-5-fable-low                                                         Claude Fable 5 Low  [1M context, $10 / 1M Input · $1 / 1M Cached input · $50 / 1M Output]
  claude-5-fable-medium                                                      Claude Fable 5 Medium  [1M context, $10 / 1M Input · $1 / 1M Cached input · $50 / 1M Output]
  claude-5-fable-high                                                        Claude Fable 5 High  [1M context, $10 / 1M Input · $1 / 1M Cached input · $50 / 1M Output]
  claude-5-fable-xhigh                                                       Claude Fable 5 XHigh  [1M context, $10 / 1M Input · $1 / 1M Cached input · $50 / 1M Output]
  claude-5-fable-max                                                         Claude Fable 5 Max  [1M context, $10 / 1M Input · $1 / 1M Cached input · $50 / 1M Output]

Claude Fable 5.1 (claude-fable-5.1)
  aliases: fable
  claude-fable-5-1-low                                                       Claude Fable 5.1 Low  [1M context, $10 / 1M Input · $0.25 / 1M Cached input · $50 / 1M Output]
  claude-fable-5-1-medium                                                    Claude Fable 5.1 Medium  [1M context, $10 / 1M Input · $0.25 / 1M Cached input · $50 / 1M Output]
  claude-fable-5-1-high                                                      Claude Fable 5.1 High  [1M context, $10 / 1M Input · $0.25 / 1M Cached input · $50 / 1M Output]
  claude-fable-5-1-xhigh                                                     Claude Fable 5.1 XHigh  [1M context, $10 / 1M Input · $0.25 / 1M Cached input · $50 / 1M Output]
  claude-fable-5-1-max                                                       Claude Fable 5.1 Max  [1M context, $10 / 1M Input · $0.25 / 1M Cached input · $50 / 1M Output]

Claude Sonnet 5 (claude-sonnet-5)
  aliases: claude, sonnet
  claude-sonnet-5-low                                                        Claude Sonnet 5 Low  [1M context, $2 / 1M Input · $0.2 / 1M Cached input · $10 / 1M Output]
  claude-sonnet-5-medium                                                     Claude Sonnet 5 Medium  [1M context, $2 / 1M Input · $0.2 / 1M Cached input · $10 / 1M Output]
  claude-sonnet-5-high                                                       Claude Sonnet 5 High  [1M context, $2 / 1M Input · $0.2 / 1M Cached input · $10 / 1M Output]
  claude-sonnet-5-xhigh                                                      Claude Sonnet 5 XHigh  [1M context, $2 / 1M Input · $0.2 / 1M Cached input · $10 / 1M Output]
  claude-sonnet-5-max                                                        Claude Sonnet 5 Max  [1M context, $2 / 1M Input · $0.2 / 1M Cached input · $10 / 1M Output]

Claude Haiku 5.5 (claude-haiku-5.5)
  claude-haiku-5-5-low                                                       Claude Haiku 5.5 Low  [1M context, $0.1 / 1M Input · $0.01 / 1M Cached input · $0.5 / 1M Output]
  claude-haiku-5-5-medium                                                    Claude Haiku 5.5 Medium  [1M context, $0.1 / 1M Input · $0.01 / 1M Cached input · $0.5 / 1M Output]
  claude-haiku-5-5-high                                                      Claude Haiku 5.5 High  [1M context, $0.1 / 1M Input · $0.01 / 1M Cached input · $0.5 / 1M Output]
  claude-haiku-5-5-xhigh                                                     Claude Haiku 5.5 XHigh  [1M context, $0.1 / 1M Input · $0.01 / 1M Cached input · $0.5 / 1M Output]
  claude-haiku-5-5-max                                                       Claude Haiku 5.5 Max  [1M context, $0.1 / 1M Input · $0.01 / 1M Cached input · $0.5 / 1M Output]

Gemini 3.5 Flash (gemini-3.5-flash)
  gemini-3-5-flash-minimal                                                   Gemini 3.5 Flash Minimal  [1.05M context, $1.5 / 1M Input · $0.15 / 1M Cached input · $9 / 1M Output]
  gemini-3-5-flash-low                                                       Gemini 3.5 Flash Low  [1.05M context, $1.5 / 1M Input · $0.15 / 1M Cached input · $9 / 1M Output]
  gemini-3-5-flash-medium                                                    Gemini 3.5 Flash Medium  [1.05M context, $1.5 / 1M Input · $0.15 / 1M Cached input · $9 / 1M Output]
  gemini-3-5-flash-high                                                      Gemini 3.5 Flash High  [1.05M context, $1.5 / 1M Input · $0.15 / 1M Cached input · $9 / 1M Output]

Gemini 3.6 Flash (gemini-3.6-flash)
  gemini-3-6-flash-minimal                                                   Gemini 3.6 Flash Minimal  [1.05M context, $1.5 / 1M Input · $0.15 / 1M Cached input · $7.5 / 1M Output]
  gemini-3-6-flash-low                                                       Gemini 3.6 Flash Low  [1.05M context, $1.5 / 1M Input · $0.15 / 1M Cached input · $7.5 / 1M Output]
  gemini-3-6-flash-medium                                                    Gemini 3.6 Flash Medium  [1.05M context, $1.5 / 1M Input · $0.15 / 1M Cached input · $7.5 / 1M Output]
  gemini-3-6-flash-high                                                      Gemini 3.6 Flash High  [1.05M context, $1.5 / 1M Input · $0.15 / 1M Cached input · $7.5 / 1M Output]

Gemini 3.7 Flash (gemini-3.7-flash)
  gemini-3-7-flash-low                                                       Gemini 3.7 Flash Low  [1.05M context, $0.75 / 1M Input · $0.08 / 1M Cached input · $3.75 / 1M Output]
  gemini-3-7-flash-medium                                                    Gemini 3.7 Flash Medium  [1.05M context, $0.75 / 1M Input · $0.08 / 1M Cached input · $3.75 / 1M Output]
  gemini-3-7-flash-high                                                      Gemini 3.7 Flash High  [1.05M context, $0.75 / 1M Input · $0.08 / 1M Cached input · $3.75 / 1M Output]

Gemini 4 Argon (gemini-4-argon)
  gemini-4-argon-low                                                         Gemini 4 Argon Low  [1.05M context, $2 / 1M Input · $0.1 / 1M Cached input · $10 / 1M Output]
  gemini-4-argon-medium                                                      Gemini 4 Argon Medium  [1.05M context, $2 / 1M Input · $0.1 / 1M Cached input · $10 / 1M Output]
  gemini-4-argon-high                                                        Gemini 4 Argon High  [1.05M context, $2 / 1M Input · $0.1 / 1M Cached input · $10 / 1M Output]

Gemini 4 Hydrogen (gemini-4-hydrogen)
  gemini-4-hydrogen-low                                                      Gemini 4 Hydrogen Low  [1.05M context, $1.5 / 1M Input · $0.08 / 1M Cached input · $7.5 / 1M Output]
  gemini-4-hydrogen-medium                                                   Gemini 4 Hydrogen Medium  [1.05M context, $1.5 / 1M Input · $0.08 / 1M Cached input · $7.5 / 1M Output]
  gemini-4-hydrogen-high                                                     Gemini 4 Hydrogen High  [1.05M context, $1.5 / 1M Input · $0.08 / 1M Cached input · $7.5 / 1M Output]
  gemini-4-hydrogen-xhigh                                                    Gemini 4 Hydrogen XHigh  [1.05M context, $1.5 / 1M Input · $0.08 / 1M Cached input · $7.5 / 1M Output]

Gemini 4 Neon (gemini-4-neon)
  gemini-4-neon-low                                                          Gemini 4 Neon Low  [1.05M context, $3 / 1M Input · $0.15 / 1M Cached input · $15 / 1M Output]
  gemini-4-neon-medium                                                       Gemini 4 Neon Medium  [1.05M context, $3 / 1M Input · $0.15 / 1M Cached input · $15 / 1M Output]
  gemini-4-neon-high                                                         Gemini 4 Neon High  [1.05M context, $3 / 1M Input · $0.15 / 1M Cached input · $15 / 1M Output]
  gemini-4-neon-xhigh                                                        Gemini 4 Neon XHigh  [1.05M context, $3 / 1M Input · $0.15 / 1M Cached input · $15 / 1M Output]

GPT-5.6 Sol (gpt-5.6-sol)
  gpt-5-6-sol-none                                                           GPT-5.6 Sol No Thinking  [1M context, $5 / 1M Input · $0.5 / 1M Cached input · $30 / 1M Output]
  gpt-5-6-sol-low                                                            GPT-5.6 Sol Low Thinking  [1M context, $5 / 1M Input · $0.5 / 1M Cached input · $30 / 1M Output]
  gpt-5-6-sol-medium                                                         GPT-5.6 Sol Medium Thinking  [1M context, $5 / 1M Input · $0.5 / 1M Cached input · $30 / 1M Output]
  gpt-5-6-sol-high                                                           GPT-5.6 Sol High Thinking  [1M context, $5 / 1M Input · $0.5 / 1M Cached input · $30 / 1M Output]
  gpt-5-6-sol-xhigh                                                          GPT-5.6 Sol XHigh Thinking  [1M context, $5 / 1M Input · $0.5 / 1M Cached input · $30 / 1M Output]
  gpt-5-6-sol-max                                                            GPT-5.6 Sol Max Thinking  [1M context, $5 / 1M Input · $0.5 / 1M Cached input · $30 / 1M Output]
  gpt-5-6-sol-none-priority                                                  GPT-5.6 Sol No Thinking Fast  [1M context, $10 / 1M Input · $1 / 1M Cached input · $60 / 1M Output]
  gpt-5-6-sol-low-priority                                                   GPT-5.6 Sol Low Thinking Fast  [1M context, $10 / 1M Input · $1 / 1M Cached input · $60 / 1M Output]
  gpt-5-6-sol-medium-priority                                                GPT-5.6 Sol Medium Thinking Fast  [1M context, $10 / 1M Input · $1 / 1M Cached input · $60 / 1M Output]
  gpt-5-6-sol-high-priority                                                  GPT-5.6 Sol High Thinking Fast  [1M context, $10 / 1M Input · $1 / 1M Cached input · $60 / 1M Output]
  gpt-5-6-sol-xhigh-priority                                                 GPT-5.6 Sol XHigh Thinking Fast  [1M context, $10 / 1M Input · $1 / 1M Cached input · $60 / 1M Output]
  gpt-5-6-sol-max-priority                                                   GPT-5.6 Sol Max Thinking Fast  [1M context, $10 / 1M Input · $1 / 1M Cached input · $60 / 1M Output]

GPT-5.6 Terra (gpt-5.6-terra)
  gpt-5-6-terra-none                                                         GPT-5.6 Terra No Thinking  [1M context, $2 / 1M Input · $0.2 / 1M Cached input · $12 / 1M Output]
  gpt-5-6-terra-low                                                          GPT-5.6 Terra Low Thinking  [1M context, $2 / 1M Input · $0.2 / 1M Cached input · $12 / 1M Output]
  gpt-5-6-terra-medium                                                       GPT-5.6 Terra Medium Thinking  [1M context, $2 / 1M Input · $0.2 / 1M Cached input · $12 / 1M Output]
  gpt-5-6-terra-high                                                         GPT-5.6 Terra High Thinking  [1M context, $2 / 1M Input · $0.2 / 1M Cached input · $12 / 1M Output]
  gpt-5-6-terra-xhigh                                                        GPT-5.6 Terra XHigh Thinking  [1M context, $2 / 1M Input · $0.2 / 1M Cached input · $12 / 1M Output]
  gpt-5-6-terra-max                                                          GPT-5.6 Terra Max Thinking  [1M context, $2 / 1M Input · $0.2 / 1M Cached input · $12 / 1M Output]
  gpt-5-6-terra-none-priority                                                GPT-5.6 Terra No Thinking Fast  [1M context, $4 / 1M Input · $0.4 / 1M Cached input · $24 / 1M Output]
  gpt-5-6-terra-low-priority                                                 GPT-5.6 Terra Low Thinking Fast  [1M context, $4 / 1M Input · $0.4 / 1M Cached input · $24 / 1M Output]
  gpt-5-6-terra-medium-priority                                              GPT-5.6 Terra Medium Thinking Fast  [1M context, $4 / 1M Input · $0.4 / 1M Cached input · $24 / 1M Output]
  gpt-5-6-terra-high-priority                                                GPT-5.6 Terra High Thinking Fast  [1M context, $4 / 1M Input · $0.4 / 1M Cached input · $24 / 1M Output]
  gpt-5-6-terra-xhigh-priority                                               GPT-5.6 Terra XHigh Thinking Fast  [1M context, $4 / 1M Input · $0.4 / 1M Cached input · $24 / 1M Output]
  gpt-5-6-terra-max-priority                                                 GPT-5.6 Terra Max Thinking Fast  [1M context, $4 / 1M Input · $0.4 / 1M Cached input · $24 / 1M Output]

GPT-5.6 Luna (gpt-5.6-luna)
  gpt-5-6-luna-none                                                          GPT-5.6 Luna No Thinking  [1M context, $0.2 / 1M Input · $0.02 / 1M Cached input · $1.2 / 1M Output]
  gpt-5-6-luna-low                                                           GPT-5.6 Luna Low Thinking  [1M context, $0.2 / 1M Input · $0.02 / 1M Cached input · $1.2 / 1M Output]
  gpt-5-6-luna-medium                                                        GPT-5.6 Luna Medium Thinking  [1M context, $0.2 / 1M Input · $0.02 / 1M Cached input · $1.2 / 1M Output]
  gpt-5-6-luna-high                                                          GPT-5.6 Luna High Thinking  [1M context, $0.2 / 1M Input · $0.02 / 1M Cached input · $1.2 / 1M Output]
  gpt-5-6-luna-xhigh                                                         GPT-5.6 Luna XHigh Thinking  [1M context, $0.2 / 1M Input · $0.02 / 1M Cached input · $1.2 / 1M Output]
  gpt-5-6-luna-max                                                           GPT-5.6 Luna Max Thinking  [1M context, $0.2 / 1M Input · $0.02 / 1M Cached input · $1.2 / 1M Output]
  gpt-5-6-luna-none-priority                                                 GPT-5.6 Luna No Thinking Fast  [1M context, $0.4 / 1M Input · $0.04 / 1M Cached input · $2.4 / 1M Output]
  gpt-5-6-luna-low-priority                                                  GPT-5.6 Luna Low Thinking Fast  [1M context, $0.4 / 1M Input · $0.04 / 1M Cached input · $2.4 / 1M Output]
  gpt-5-6-luna-medium-priority                                               GPT-5.6 Luna Medium Thinking Fast  [1M context, $0.4 / 1M Input · $0.04 / 1M Cached input · $2.4 / 1M Output]
  gpt-5-6-luna-high-priority                                                 GPT-5.6 Luna High Thinking Fast  [1M context, $0.4 / 1M Input · $0.04 / 1M Cached input · $2.4 / 1M Output]
  gpt-5-6-luna-xhigh-priority                                                GPT-5.6 Luna XHigh Thinking Fast  [1M context, $0.4 / 1M Input · $0.04 / 1M Cached input · $2.4 / 1M Output]
  gpt-5-6-luna-max-priority                                                  GPT-5.6 Luna Max Thinking Fast  [1M context, $0.4 / 1M Input · $0.04 / 1M Cached input · $2.4 / 1M Output]

GPT-6.2 Sol (gpt-6.2-sol)
  gpt-6-2-sol-low                                                            GPT-6.2 Sol Low Thinking  [1.05M context, $2 / 1M Input · $0.1 / 1M Cached input · $10 / 1M Output, new]
  gpt-6-2-sol-medium                                                         GPT-6.2 Sol Medium Thinking  [1.05M context, $2 / 1M Input · $0.1 / 1M Cached input · $10 / 1M Output, new]
  gpt-6-2-sol-high                                                           GPT-6.2 Sol High Thinking  [1.05M context, $2 / 1M Input · $0.1 / 1M Cached input · $10 / 1M Output, new]
  gpt-6-2-sol-xhigh                                                          GPT-6.2 Sol XHigh Thinking  [1.05M context, $2 / 1M Input · $0.1 / 1M Cached input · $10 / 1M Output, new]
  gpt-6-2-sol-max                                                            GPT-6.2 Sol Max Thinking  [1.05M context, $2 / 1M Input · $0.1 / 1M Cached input · $10 / 1M Output, new]
  gpt-6-2-sol-low-priority                                                   GPT-6.2 Sol Low Thinking Fast  [1.05M context, $4 / 1M Input · $0.2 / 1M Cached input · $20 / 1M Output, new]
  gpt-6-2-sol-medium-priority                                                GPT-6.2 Sol Medium Thinking Fast  [1.05M context, $4 / 1M Input · $0.2 / 1M Cached input · $20 / 1M Output, new]
  gpt-6-2-sol-high-priority                                                  GPT-6.2 Sol High Thinking Fast  [1.05M context, $4 / 1M Input · $0.2 / 1M Cached input · $20 / 1M Output, new]
  gpt-6-2-sol-xhigh-priority                                                 GPT-6.2 Sol XHigh Thinking Fast  [1.05M context, $4 / 1M Input · $0.2 / 1M Cached input · $20 / 1M Output, new]
  gpt-6-2-sol-max-priority                                                   GPT-6.2 Sol Max Thinking Fast  [1.05M context, $4 / 1M Input · $0.2 / 1M Cached input · $20 / 1M Output, new]

GPT-6.1 Sol (gpt-6.1-sol)
  gpt-6-1-sol-low                                                            GPT-6.1 Sol Low Thinking  [1M context, $2 / 1M Input · $0.1 / 1M Cached input · $10 / 1M Output]
  gpt-6-1-sol-medium                                                         GPT-6.1 Sol Medium Thinking  [1M context, $2 / 1M Input · $0.1 / 1M Cached input · $10 / 1M Output]
  gpt-6-1-sol-high                                                           GPT-6.1 Sol High Thinking  [1M context, $2 / 1M Input · $0.1 / 1M Cached input · $10 / 1M Output]
  gpt-6-1-sol-xhigh                                                          GPT-6.1 Sol XHigh Thinking  [1M context, $2 / 1M Input · $0.1 / 1M Cached input · $10 / 1M Output]
  gpt-6-1-sol-max                                                            GPT-6.1 Sol Max Thinking  [1M context, $2 / 1M Input · $0.1 / 1M Cached input · $10 / 1M Output]
  gpt-6-1-sol-low-priority                                                   GPT-6.1 Sol Low Thinking Fast  [1M context, $4 / 1M Input · $0.2 / 1M Cached input · $20 / 1M Output]
  gpt-6-1-sol-medium-priority                                                GPT-6.1 Sol Medium Thinking Fast  [1M context, $4 / 1M Input · $0.2 / 1M Cached input · $20 / 1M Output]
  gpt-6-1-sol-high-priority                                                  GPT-6.1 Sol High Thinking Fast  [1M context, $4 / 1M Input · $0.2 / 1M Cached input · $20 / 1M Output]
  gpt-6-1-sol-xhigh-priority                                                 GPT-6.1 Sol XHigh Thinking Fast  [1M context, $4 / 1M Input · $0.2 / 1M Cached input · $20 / 1M Output]
  gpt-6-1-sol-max-priority                                                   GPT-6.1 Sol Max Thinking Fast  [1M context, $4 / 1M Input · $0.2 / 1M Cached input · $20 / 1M Output]

GPT-6.1 Terra (gpt-6.1-terra)
  gpt-6-1-terra-low                                                          GPT-6.1 Terra Low Thinking  [1M context, $1 / 1M Input · $0.05 / 1M Cached input · $5 / 1M Output]
  gpt-6-1-terra-medium                                                       GPT-6.1 Terra Medium Thinking  [1M context, $1 / 1M Input · $0.05 / 1M Cached input · $5 / 1M Output]
  gpt-6-1-terra-high                                                         GPT-6.1 Terra High Thinking  [1M context, $1 / 1M Input · $0.05 / 1M Cached input · $5 / 1M Output]
  gpt-6-1-terra-xhigh                                                        GPT-6.1 Terra XHigh Thinking  [1M context, $1 / 1M Input · $0.05 / 1M Cached input · $5 / 1M Output]
  gpt-6-1-terra-max                                                          GPT-6.1 Terra Max Thinking  [1M context, $1 / 1M Input · $0.05 / 1M Cached input · $5 / 1M Output]
  gpt-6-1-terra-low-priority                                                 GPT-6.1 Terra Low Thinking Fast  [1M context, $2 / 1M Input · $0.1 / 1M Cached input · $10 / 1M Output]
  gpt-6-1-terra-medium-priority                                              GPT-6.1 Terra Medium Thinking Fast  [1M context, $2 / 1M Input · $0.1 / 1M Cached input · $10 / 1M Output]
  gpt-6-1-terra-high-priority                                                GPT-6.1 Terra High Thinking Fast  [1M context, $2 / 1M Input · $0.1 / 1M Cached input · $10 / 1M Output]
  gpt-6-1-terra-xhigh-priority                                               GPT-6.1 Terra XHigh Thinking Fast  [1M context, $2 / 1M Input · $0.1 / 1M Cached input · $10 / 1M Output]
  gpt-6-1-terra-max-priority                                                 GPT-6.1 Terra Max Thinking Fast  [1M context, $2 / 1M Input · $0.1 / 1M Cached input · $10 / 1M Output]

GLM-5.2 (glm-5.2)
  glm-5-2                                                                    GLM-5.2 High  [200K context, $1.4 / 1M Input · $0.26 / 1M Cached input · $4.4 / 1M Output]
  glm-5-2-max                                                                GLM-5.2 Max  [200K context, $1.4 / 1M Input · $0.26 / 1M Cached input · $4.4 / 1M Output]
  glm-5-2-1m                                                                 GLM-5.2 High 1M  [1M context, $1.4 / 1M Input · $0.26 / 1M Cached input · $4.4 / 1M Output]
  glm-5-2-max-1m                                                             GLM-5.2 Max 1M  [1M context, $1.4 / 1M Input · $0.26 / 1M Cached input · $4.4 / 1M Output]
  glm-5-2-none                                                               GLM-5.2 No Thinking  [200K context, $1.4 / 1M Input · $0.26 / 1M Cached input · $4.4 / 1M Output]
  glm-5-2-none-1m                                                            GLM-5.2 No Thinking 1M  [1M context, $1.4 / 1M Input · $0.26 / 1M Cached input · $4.4 / 1M Output]

Grok 4.6 (grok-4.6)
  grok-4-6-low                                                               Grok 4.6 Low  [500K context, $2 / 1M Input · $0.3 / 1M Cached input · $6 / 1M Output, beta]
  grok-4-6-medium                                                            Grok 4.6 Medium  [500K context, $2 / 1M Input · $0.3 / 1M Cached input · $6 / 1M Output, beta]
  grok-4-6-high                                                              Grok 4.6 High  [500K context, $2 / 1M Input · $0.3 / 1M Cached input · $6 / 1M Output, beta]
  grok-4-6-xhigh                                                             Grok 4.6 XHigh  [500K context, $2 / 1M Input · $0.3 / 1M Cached input · $6 / 1M Output, beta]

Grok 4.7 (grok-4.7)
  grok-4-7-low                                                               Grok 4.7 Low  [500K context, $2 / 1M Input · $0.5 / 1M Cached input · $6 / 1M Output]
  grok-4-7-medium                                                            Grok 4.7 Medium  [500K context, $2 / 1M Input · $0.5 / 1M Cached input · $6 / 1M Output]
  grok-4-7-high                                                              Grok 4.7 High  [500K context, $2 / 1M Input · $0.5 / 1M Cached input · $6 / 1M Output]
  grok-4-7-xhigh                                                             Grok 4.7 XHigh  [500K context, $2 / 1M Input · $0.5 / 1M Cached input · $6 / 1M Output]

Muse Wonder (muse-wonder)
  muse-wonder-1-0-minimal                                                    Muse Wonder Minimal  [1.05M context, $4 / 1M Input · $0.2 / 1M Cached input · $20 / 1M Output]
  muse-wonder-1-0-low                                                        Muse Wonder Low  [1.05M context, $4 / 1M Input · $0.2 / 1M Cached input · $20 / 1M Output]
  muse-wonder-1-0-medium                                                     Muse Wonder Medium  [1.05M context, $4 / 1M Input · $0.2 / 1M Cached input · $20 / 1M Output]
  muse-wonder-1-0-high                                                       Muse Wonder High  [1.05M context, $4 / 1M Input · $0.2 / 1M Cached input · $20 / 1M Output]
  muse-wonder-1-0-xhigh                                                      Muse Wonder XHigh  [1.05M context, $4 / 1M Input · $0.2 / 1M Cached input · $20 / 1M Output]
  muse-wonder-1-0-max                                                        Muse Wonder Max  [1.05M context, $4 / 1M Input · $0.2 / 1M Cached input · $20 / 1M Output]

Nemotron 3.5 Super (nemotron-3.5-super)
  nemotron-3-5-super-none                                                    Nemotron 3.5 Super None  [1.05M context, $0.2 / 1M Input · $0.04 / 1M Cached input · $0.5 / 1M Output]
  nemotron-3-5-super-medium                                                  Nemotron 3.5 Super Medium  [1.05M context, $0.2 / 1M Input · $0.04 / 1M Cached input · $0.5 / 1M Output]
  nemotron-3-5-super-high                                                    Nemotron 3.5 Super High  [1.05M context, $0.2 / 1M Input · $0.04 / 1M Cached input · $0.5 / 1M Output]

Nemotron 3.5 Lightning (nemotron-3.5-lightning)
  nemotron-3-5-lightning-none                                                Nemotron 3.5 Lightning None  [1.05M context, $0.05 / 1M Input · $0.01 / 1M Cached input · $0.2 / 1M Output]
  nemotron-3-5-lightning-high                                                Nemotron 3.5 Lightning High  [1.05M context, $0.05 / 1M Input · $0.01 / 1M Cached input · $0.2 / 1M Output]

Kimi K3 (kimi-k3)
  kimi-k3-low                                                                Kimi K3 Low  [1.05M context, $3 / 1M Input · $0.3 / 1M Cached input · $15 / 1M Output]
  kimi-k3-high                                                               Kimi K3 High  [1.05M context, $3 / 1M Input · $0.3 / 1M Cached input · $15 / 1M Output]
  kimi-k3-max                                                                Kimi K3 Max  [1.05M context, $3 / 1M Input · $0.3 / 1M Cached input · $15 / 1M Output]

GLM-5.3 Flash (glm-5.3-flash)
  glm-5-3-flash-low                                                          GLM-5.3 Flash Low  [1M context, $0.15 / 1M Input · $0.03 / 1M Cached input · $0.5 / 1M Output]
  glm-5-3-flash-high                                                         GLM-5.3 Flash High  [1M context, $0.15 / 1M Input · $0.03 / 1M Cached input · $0.5 / 1M Output]
  glm-5-3-flash-max                                                          GLM-5.3 Flash Max  [1M context, $0.15 / 1M Input · $0.03 / 1M Cached input · $0.5 / 1M Output]

DeepSeek V4 Flash (deepseek-v4-flash)
  deepseek-v4-flash-high                                                     DeepSeek V4 Flash High  [1.05M context, $0.14 / 1M Input · $0.03 / 1M Cached input · $0.28 / 1M Output]
  deepseek-v4-flash-max                                                      DeepSeek V4 Flash Max  [1.05M context, $0.14 / 1M Input · $0.03 / 1M Cached input · $0.28 / 1M Output]

DeepSeek V4.1 Flash (deepseek-v4.1-flash)
  deepseek-v4-1-flash-1017-none                                              DeepSeek V4.1 Flash None  [1.05M context, $0.22 / 1M Input · $0.01 / 1M Cached input · $0.66 / 1M Output]
  deepseek-v4-1-flash-1017-low                                               DeepSeek V4.1 Flash Low  [1.05M context, $0.22 / 1M Input · $0.01 / 1M Cached input · $0.66 / 1M Output]
  deepseek-v4-1-flash-1017-high                                              DeepSeek V4.1 Flash High  [1.05M context, $0.22 / 1M Input · $0.01 / 1M Cached input · $0.66 / 1M Output]
  deepseek-v4-1-flash-1017-max                                               DeepSeek V4.1 Flash Max  [1.05M context, $0.22 / 1M Input · $0.01 / 1M Cached input · $0.66 / 1M Output]

SWE-1.7 (swe-1.7)
  swe-1-7                                                                    SWE-1.7 Max  [262K context, $0.5 / 1M Input · $0.2 / 1M Cached input · $2.5 / 1M Output]
  swe-1-7-medium                                                             SWE-1.7 Medium  [262K context, $0.5 / 1M Input · $0.2 / 1M Cached input · $2.5 / 1M Output]

Fusion (fusion)

Claude Opus 4.6 (claude-opus-4.6)
  claude-opus-4-6                                                            Claude Opus 4.6  [200K context, $5 / 1M Input · $0.5 / 1M Cached input · $25 / 1M Output]
  claude-opus-4-6-thinking                                                   Claude Opus 4.6 Thinking  [200K context, $5 / 1M Input · $0.5 / 1M Cached input · $25 / 1M Output]
  claude-opus-4-6-1m                                                         Claude Opus 4.6 1M  [1M context, $5 / 1M Input · $0.5 / 1M Cached input · $25 / 1M Output]
  claude-opus-4-6-thinking-1m                                                Claude Opus 4.6 Thinking 1M  [1M context, $5 / 1M Input · $0.5 / 1M Cached input · $25 / 1M Output]

GPT-5.4 (gpt-5.4)
  gpt-5-4-none                                                               GPT-5.4 No Thinking  [272K context, $2.5 / 1M Input · $0.25 / 1M Cached input · $15 / 1M Output]
  gpt-5-4-low                                                                GPT-5.4 Low Thinking  [272K context, $2.5 / 1M Input · $0.25 / 1M Cached input · $15 / 1M Output]
  gpt-5-4-medium                                                             GPT-5.4 Medium Thinking  [272K context, $2.5 / 1M Input · $0.25 / 1M Cached input · $15 / 1M Output]
  gpt-5-4-high                                                               GPT-5.4 High Thinking  [272K context, $2.5 / 1M Input · $0.25 / 1M Cached input · $15 / 1M Output]
  gpt-5-4-xhigh                                                              GPT-5.4 XHigh Thinking  [272K context, $2.5 / 1M Input · $0.25 / 1M Cached input · $15 / 1M Output]
  gpt-5-4-none-priority                                                      GPT-5.4 No Thinking Fast  [272K context, $5 / 1M Input · $0.5 / 1M Cached input · $30 / 1M Output]
  gpt-5-4-low-priority                                                       GPT-5.4 Low Thinking Fast  [272K context, $5 / 1M Input · $0.5 / 1M Cached input · $30 / 1M Output]
  gpt-5-4-medium-priority                                                    GPT-5.4 Medium Thinking Fast  [272K context, $5 / 1M Input · $0.5 / 1M Cached input · $30 / 1M Output]
  gpt-5-4-high-priority                                                      GPT-5.4 High Thinking Fast  [272K context, $5 / 1M Input · $0.5 / 1M Cached input · $30 / 1M Output]
  gpt-5-4-xhigh-priority                                                     GPT-5.4 XHigh Thinking Fast  [272K context, $5 / 1M Input · $0.5 / 1M Cached input · $30 / 1M Output]

GPT-5.5 (gpt-5.5)
  aliases: gpt
  gpt-5-5-none                                                               GPT-5.5 No Thinking  [272K context, $5 / 1M Input · $0.5 / 1M Cached input · $30 / 1M Output]
  gpt-5-5-low                                                                GPT-5.5 Low Thinking  [272K context, $5 / 1M Input · $0.5 / 1M Cached input · $30 / 1M Output]
  gpt-5-5-medium                                                             GPT-5.5 Medium Thinking  [272K context, $5 / 1M Input · $0.5 / 1M Cached input · $30 / 1M Output]
  gpt-5-5-high                                                               GPT-5.5 High Thinking  [272K context, $5 / 1M Input · $0.5 / 1M Cached input · $30 / 1M Output]
  gpt-5-5-xhigh                                                              GPT-5.5 XHigh Thinking  [272K context, $5 / 1M Input · $0.5 / 1M Cached input · $30 / 1M Output]
  gpt-5-5-none-priority                                                      GPT-5.5 No Thinking Fast  [272K context, $12.5 / 1M Input · $1.25 / 1M Cached input · $75 / 1M Output]
  gpt-5-5-low-priority                                                       GPT-5.5 Low Thinking Fast  [272K context, $12.5 / 1M Input · $1.25 / 1M Cached input · $75 / 1M Output]
  gpt-5-5-medium-priority                                                    GPT-5.5 Medium Thinking Fast  [272K context, $12.5 / 1M Input · $1.25 / 1M Cached input · $75 / 1M Output]
  gpt-5-5-high-priority                                                      GPT-5.5 High Thinking Fast  [272K context, $12.5 / 1M Input · $1.25 / 1M Cached input · $75 / 1M Output]
  gpt-5-5-xhigh-priority                                                     GPT-5.5 XHigh Thinking Fast  [272K context, $12.5 / 1M Input · $1.25 / 1M Cached input · $75 / 1M Output]

GPT-5.4 Mini (gpt-5.4-mini)
  gpt-5-4-mini-low                                                           GPT-5.4 Mini Low Thinking  [400K context, $0.75 / 1M Input · $0.08 / 1M Cached input · $4.5 / 1M Output]
  gpt-5-4-mini-medium                                                        GPT-5.4 Mini Medium Thinking  [400K context, $0.75 / 1M Input · $0.08 / 1M Cached input · $4.5 / 1M Output]
  gpt-5-4-mini-high                                                          GPT-5.4 Mini High Thinking  [400K context, $0.75 / 1M Input · $0.08 / 1M Cached input · $4.5 / 1M Output]
  gpt-5-4-mini-xhigh                                                         GPT-5.4 Mini XHigh Thinking  [400K context, $0.75 / 1M Input · $0.08 / 1M Cached input · $4.5 / 1M Output]

Claude Sonnet 4.6 (claude-sonnet-4.6)
  claude-sonnet-4-6                                                          Claude Sonnet 4.6  [200K context, $3 / 1M Input · $0.3 / 1M Cached input · $15 / 1M Output]
  claude-sonnet-4-6-thinking                                                 Claude Sonnet 4.6 Thinking  [200K context, $3 / 1M Input · $0.3 / 1M Cached input · $15 / 1M Output]
  claude-sonnet-4-6-1m                                                       Claude Sonnet 4.6 1M  [1M context, $3 / 1M Input · $0.3 / 1M Cached input · $15 / 1M Output]
  claude-sonnet-4-6-thinking-1m                                              Claude Sonnet 4.6 Thinking 1M  [1M context, $3 / 1M Input · $0.3 / 1M Cached input · $15 / 1M Output]

Kimi K2.6 (kimi-k2.6)
  kimi-k2-6                                                                  Kimi K2.6  [262K context, $0.95 / 1M Input · $0.16 / 1M Cached input · $4 / 1M Output]

Kimi K2.7 (kimi-k2.7)
  kimi-k2-7                                                                  Kimi K2.7  [262K context, $0.95 / 1M Input · $0.19 / 1M Cached input · $4 / 1M Output]

Nemotron 3 Ultra (nemotron-3-ultra)
  nemotron-3-ultra-none                                                      Nemotron 3 Ultra None  [1M context, $0.6 / 1M Input · $0.12 / 1M Cached input · $2.4 / 1M Output]
  nemotron-3-ultra-medium                                                    Nemotron 3 Ultra Medium  [1M context, $0.6 / 1M Input · $0.12 / 1M Cached input · $2.4 / 1M Output]
  nemotron-3-ultra-high                                                      Nemotron 3 Ultra High  [1M context, $0.6 / 1M Input · $0.12 / 1M Cached input · $2.4 / 1M Output]

SWE-1.6 (swe-1.6)
  swe-1-6                                                                    SWE-1.6  [200K context, $0.5 / 1M Input · $0.2 / 1M Cached input · $2.5 / 1M Output]

DeepSeek V4 Pro (deepseek-v4-pro)
  deepseek-v4-pro-high                                                       DeepSeek V4 Pro High  [1.05M context, $1.32 / 1M Input · $0.04 / 1M Cached input · $3.96 / 1M Output]
  deepseek-v4-pro-max                                                        DeepSeek V4 Pro Max  [1.05M context, $1.32 / 1M Input · $0.04 / 1M Cached input · $3.96 / 1M Output]

Pass a family slug, alias, or model UID to `--model` (e.g. `--model opus`)
or switch models in a session with `/model <name>`.
```
