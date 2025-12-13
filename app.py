from flask import Flask, request, jsonify, render_template
import subprocess

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
    
    if stream:
        def generate_output():
            try:
                # Use stdbuf or similar if buffering issues arise, but gemini likely streams
                process = subprocess.Popen(
                    ['gemini', 'chat'],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=0 # Unbuffered
                )
                
                # Write input and close stdin to signal end of input
                process.stdin.write(prompt)
                process.stdin.close()

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
                     # In a stream, we might have already sent 200 OK. 
                     # Ideally we'd send an error event, but for simple text stream:
                     if stderr:
                        yield f"\n[Error: {stderr}]"

            except Exception as e:
                yield f"\n[Exception: {str(e)}]"

        return app.response_class(generate_output(), mimetype='text/plain')

    else:
        try:
            # call gemini cli with the prompt via stdin (Buffered)
            result = subprocess.run(
                ['gemini', 'chat'],
                input=prompt,
                capture_output=True,
                text=True,
                check=False 
            )
            
            if result.returncode != 0:
                 return jsonify({
                     'error': 'Gemini CLI failed',
                     'stderr': result.stderr
                 }), 500
                 
            # The output from gemini cli is the response
            return jsonify({'response': result.stdout.strip()})
    
        except Exception as e:
            return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
