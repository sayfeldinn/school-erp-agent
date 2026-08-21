import 'package:flutter/material.dart';
import 'dart:convert';
import 'package:http/http.dart' as http;
void main() {
  runApp(const SchoolERPAgentApp());
}

class SchoolERPAgentApp extends StatelessWidget {
  const SchoolERPAgentApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'School ERP Agent',
      theme: ThemeData(
        primarySwatch: Colors.blue,
        useMaterial3: true,
      ),
      home: const ChatScreen(),
    );
  }
}

class ChatScreen extends StatefulWidget {
  const ChatScreen({super.key});

  @override
  State<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends State<ChatScreen> {
  final TextEditingController _controller = TextEditingController();
  final List<Map<String, String>> _messages = [];
  bool _isLoading = false;

  // Placeholder function for your API integration
  Future<void> sendMessageToAgent(String query) async {
    setState(() {
      _isLoading = true;
    });

    try {
      final response = await http.post(
        Uri.parse('http://127.0.0.1:8000/chat'),
        headers: {
          'Content-Type': 'application/json',
          'X-Agent-School': 'school-a',
          'X-Agent-Role': 'teacher',
          'X-Agent-User': 'teacher.ahmed@school-a.edu',
        },
        body: jsonEncode({'message': query}),
      );

      final data = jsonDecode(response.body);

      if (response.statusCode == 200) {
        final text = data['answer'] ?? 'No answer received.';
        setState(() {
          _messages.insert(0, {"sender": "Agent", "text": text});
        });
      } else if (response.statusCode == 403) {
        final code = data['error']?['code'] ?? 'forbidden';
        setState(() {
          _messages.insert(0, {"sender": "Agent", "text": "Access denied: $code"});
        });
      } else if (response.statusCode == 429) {
        setState(() {
          _messages.insert(0, {"sender": "Agent", "text": "Rate limited. Please wait and try again."});
        });
      } else {
        final detail = data['error']?['detail'] ?? 'Server error ${response.statusCode}';
        setState(() {
          _messages.insert(0, {"sender": "Agent", "text": "Error: $detail"});
        });
      }
    } catch (e) {
      setState(() {
        _messages.insert(0, {"sender": "Agent", "text": "Connection failed. The Agent server is not running yet."});
      });
    } finally {
      setState(() {
        _isLoading = false;
      });
    }
  }

  void _handleSend() {
    final text = _controller.text.trim();
    if (text.isEmpty) return;

    setState(() {
      _messages.insert(0, {"sender": "User", "text": text});
    });
    
    _controller.clear();
    sendMessageToAgent(text);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('School ERP Assistant'),
        backgroundColor: Colors.blueAccent,
        foregroundColor: Colors.white,
      ),
      body: Column(
        children: [
          Expanded(
            child: ListView.builder(
              reverse: true,
              itemCount: _messages.length,
              itemBuilder: (context, index) {
                final msg = _messages[index];
                final isUser = msg["sender"] == "User";
                return Align(
                  alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
                  child: Container(
                    margin: const EdgeInsets.symmetric(vertical: 4, horizontal: 8),
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                      color: isUser ? Colors.blue[100] : Colors.grey[200],
                      borderRadius: BorderRadius.circular(12),
                    ),
                    child: Text(msg["text"]!),
                  ),
                );
              },
            ),
          ),
          if (_isLoading)
            const Padding(
              padding: EdgeInsets.all(8.0),
              child: CircularProgressIndicator(),
            ),
          Padding(
            padding: const EdgeInsets.all(8.0),
            child: Row(
              children: [
                Expanded(
                  child: TextField(
                    controller: _controller,
                    decoration: const InputDecoration(
                      hintText: 'Ask about students, teachers, etc...',
                      border: OutlineInputBorder(),
                    ),
                    onSubmitted: (_) => _handleSend(),
                  ),
                ),
                const SizedBox(width: 8),
                IconButton(
                  icon: const Icon(Icons.send, color: Colors.blueAccent),
                  onPressed: _handleSend,
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}