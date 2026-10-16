# SDD — LCBooks: geração de vídeos a partir de PDFs

**Versão:** 1.0  
**Data:** 16/10/2026  
**Repositório analisado:** [`barrosrafa/lcBooks`](https://github.com/barrosrafa/lcBooks), branch `main`  
**Objetivo deste documento:** especificar a evolução do pipeline atual para gerar um vídeo MP4 após gerar cada roteiro, com arquitetura, etapas de implementação, arquivos, exemplos de código, testes e critérios de aceite.

---

## 1. Resumo executivo

O LCBooks já transforma PDFs com camada de texto em uma estrutura de capítulos, resumos, roteiros de 20, 3 e 1 minuto e ideias visuais. A evolução proposta é que **cada roteiro aprovado produza seu próprio vídeo**, sem esperar a finalização de todos os roteiros do livro.

Para cada capítulo, o fluxo padrão será:

1. Extrair e validar o texto do PDF.
2. Identificar capítulos e páginas.
3. Gerar um resumo rastreável às páginas da fonte.
4. Gerar e validar os roteiros de 20, 3 e 1 minuto.
5. Para cada roteiro, criar storyboard, falas segmentadas, imagens/cenas, narração, legendas e MP4.
6. Verificar tecnicamente os arquivos e salvar um manifesto com status, duração, configuração e erros.
7. Continuar do último item concluído caso o processo seja interromido.

### Decisão central de arquitetura

A primeira versão deve renderizar vídeos com **FFmpeg + imagens/cenas + narração TTS + legendas**. O vídeo final poderá conter cartões visuais, imagens fornecidas pelo usuário, imagens geradas por um provider configurável, animações leves e títulos. Isso já produz um MP4 real e publicável, sem tornar o sistema dependente de uma API de geração de clipes de vídeo.

A geração de clipes inteiramente por IA fica atrás de uma interface `VideoClipProvider`, como uma fase posterior e opcional. Essa separação evita acoplar o produto a um único fornecedor, preço, limite de duração ou formato de API.
