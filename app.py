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
    
    # Set up environment with TERM
    env = os.environ.copy()
    env['TERM'] = 'xterm-256color'

    if stream:
        def generate_output():
            try:
                # Pass prompt as argument to avoid stdin buffering issues
                process = subprocess.Popen(
                    ['gemini', prompt],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=0, # Unbuffered
                    env=env
                )
                
                # We don't use stdin anymore
                print("Started process with prompt arg. Reading stdout...", flush=True)

                # Read stdout char by char or line by line
                # For proper token streaming, char by char (or small chunk) is best
                while True:
                    # Read a chunk
                    chunk = process.stdout.read(1) 
                    if not chunk and process.poll() is not None:
                        break
                    if chunk:
                        yield chunk
                
                # Check for errors after
                stderr = process.stderr.read()
                if process.returncode != 0:
                     print(f"Process failed with code {process.returncode}: {stderr}", flush=True)
                     if stderr:
                        yield f"\n[Error: {stderr}]"

            except Exception as e:
                print(f"Exception during generation: {e}", flush=True)
                yield f"\n[Exception: {str(e)}]"

        return app.response_class(generate_output(), mimetype='text/plain')

    else:
        try:
            print("Running subprocess.run with prompt arg...", flush=True)
            # call gemini cli with the prompt as argument
            result = subprocess.run(
                ['gemini', prompt],
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
                 
            # The output from gemini cli is the response
            return jsonify({'response': result.stdout.strip()})
    
        except Exception as e:
            print(f"Exception: {e}", flush=True)
            return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
