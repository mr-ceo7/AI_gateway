from flask import Flask, request, jsonify, render_template
import subprocess
import os

app = Flask(__name__)

@app.route('/')
def home():
    return render_template('index.html')


@app.route('/api/generate', methods=['POST'])
def generate():
    data = request.get_json()
    
    if not data:
        return jsonify({'error': 'Missing request body'}), 400

    prompt = ""
    if 'messages' in data:
        # Context Mode: Construct prompt from history
        for msg in data['messages']:
            role = "User" if msg['role'] == 'user' else "Model"
            prompt += f"{role}: {msg['content']}\n"
    elif 'prompt' in data:
        # Stateless Mode
        prompt = data['prompt']
    else:
        return jsonify({'error': 'Missing prompt or messages'}), 400

    stream = data.get('stream', False)
    # Add debug logging
    print(f"Generating with prompt length: {len(prompt)}", flush=True)
    
    # Set up environment - mimic simple shell
    env = os.environ.copy()
    env['TERM'] = 'dumb' # Force non-interactive

    if stream:
        def generate_output():
            try:
                # Use ['gemini'] processing stdin, merging stderr to avoid deadlock
                process = subprocess.Popen(
                    ['gemini'],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, # Merge stderr
                    text=True,
                    bufsize=0, # Unbuffered
                    env=env
                )
                
                print("Writing to stdin...", flush=True)
                # Ensure newline
                if not prompt.endswith('\n'):
                    prompt_in = prompt + '\n'
                else:
                    prompt_in = prompt
                    
                process.stdin.write(prompt_in)
                process.stdin.flush()
                process.stdin.close()
                print("Stdin closed. Reading stdout (merged with stderr)...", flush=True)

                while True:
                    # Read larger chunks to avoid overhead, but small enough for streaming
                    # Note: read(1) is fine for text=True unbuffered
                    chunk = process.stdout.read(1)
                    if not chunk and process.poll() is not None:
                        break
                    if chunk:
                        yield chunk
                
                if process.returncode != 0:
                     print(f"Process exited with code {process.returncode}", flush=True)

            except Exception as e:
                print(f"Exception during generation: {e}", flush=True)
                yield f"\n[Exception: {str(e)}]"

        return app.response_class(generate_output(), mimetype='text/plain')

    else:
        try:
            print("Running subprocess.run (stdin)...", flush=True)
            result = subprocess.run(
                ['gemini'],
                input=prompt,
                capture_output=True,
                text=True,
                check=False,
                env=env
            )
            
            print(f"Subprocess finished. Code: {result.returncode}", flush=True)
            if result.returncode != 0:
                 print(f"Error output: {result.stderr}", flush=True)
                 return jsonify({
                     'error': 'Gemini CLI failed',
                     'stderr': result.stderr
                 }), 500
                 
            return jsonify({'response': result.stdout.strip()})
    
        except Exception as e:
            print(f"Exception: {e}", flush=True)
            return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
