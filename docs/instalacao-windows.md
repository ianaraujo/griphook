# Instalando o Griphook no Windows

Este guia é para quem só quer usar o Griphook no Claude. Não é preciso saber
programar, nem abrir o Prompt de Comando, nem ser administrador do computador.

## Antes de começar

- Windows 10 ou 11.
- O Claude já instalado no computador.
- Estar na rede da empresa (ou conectado à VPN).

## Passo a passo

1. Baixe o arquivo **`Griphook-Setup-1.0.0.exe`** enviado pela equipe.
2. Dê dois cliques nele. Se o Windows mostrar um aviso azul de "Windows
   protegeu o computador", clique em **Mais informações** → **Executar assim
   mesmo**.
3. Avance até a tela **Conexão com o banco de dados** e preencha:
   - **Entrar com minha conta do Windows** — deixe marcada esta opção. É ela
     que usa o seu próprio login, sem senha nenhuma para digitar.
   - **Servidor** — o endereço informado pela equipe (por exemplo,
     `servidorbdsp`).
   - **Banco de dados** — o nome informado pela equipe (por exemplo,
     `Offshore_web`).
4. Clique em **Testar conexão**. Deve aparecer *"Conexão bem-sucedida"* em
   verde. Se aparecer uma mensagem vermelha, confira o que foi digitado e
   tente de novo.
5. Clique em **Avançar** e depois em **Instalar**.
6. Ao terminar, **feche e abra o Claude novamente**.

## Como usar

Basta pedir ao Claude em português. Ele decide sozinho quando consultar o
banco. Por exemplo:

- "quantos clientes foram cadastrados este mês?"
- "quais são as colunas da tabela de pedidos?"
- "me mostra os 10 maiores contratos"

O Griphook é **somente leitura**: ele consegue consultar dados, mas nunca
alterar, apagar ou criar nada no banco.

## Se algo der errado

**"Falta um componente da Microsoft" durante a instalação**
O computador precisa do *Microsoft ODBC Driver 18 for SQL Server*. Se a
instalação oferecer instalá-lo, aceite e confirme a janela do Windows que
aparecer. Se não for possível, peça ao suporte de TI para instalar esse
componente — é só copiar o nome dele no pedido.

**O Claude diz que não encontrou o Griphook**
Feche o Claude completamente e abra de novo. A instalação só passa a valer
para programas abertos depois dela.

**"Não foi possível conectar" no teste**
Verifique se você está na rede da empresa ou na VPN, e confirme o nome do
servidor e do banco com a equipe.

## Desinstalando

Vá em **Configurações → Aplicativos → Aplicativos instalados**, procure por
**Griphook** e clique em **Desinstalar**.
