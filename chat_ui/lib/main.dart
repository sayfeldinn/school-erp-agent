import 'package:flutter/material.dart';
import 'dart:convert';
import 'package:http/http.dart' as http;

const _baseUrl = 'http://127.0.0.1:8000';

void main() {
  runApp(const SchoolERPAgentApp());
}

class SchoolERPAgentApp extends StatefulWidget {
  const SchoolERPAgentApp({super.key, this.client});

  final http.Client? client;

  @override
  State<SchoolERPAgentApp> createState() => _SchoolERPAgentAppState();
}

class _SchoolERPAgentAppState extends State<SchoolERPAgentApp> {
  late final http.Client _client;
  late final bool _ownsClient;
  String? _accessToken;

  @override
  void initState() {
    super.initState();
    _ownsClient = widget.client == null;
    _client = widget.client ?? http.Client();
  }

  void _authenticated(String accessToken) {
    setState(() {
      _accessToken = accessToken;
    });
  }

  void _clearAuthentication() {
    setState(() {
      _accessToken = null;
    });
  }

  @override
  void dispose() {
    if (_ownsClient) {
      _client.close();
    }
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'School ERP Agent',
      theme: ThemeData(
        primarySwatch: Colors.blue,
        useMaterial3: true,
      ),
      home: _accessToken == null
          ? LoginScreen(client: _client, onAuthenticated: _authenticated)
          : ChatScreen(
              client: _client,
              accessToken: _accessToken!,
              onAuthenticationRequired: _clearAuthentication,
            ),
    );
  }
}

class LoginScreen extends StatefulWidget {
  const LoginScreen({
    super.key,
    required this.client,
    required this.onAuthenticated,
  });

  final http.Client client;
  final ValueChanged<String> onAuthenticated;

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final TextEditingController _emailController = TextEditingController();
  final TextEditingController _passwordController = TextEditingController();
  bool _isLoading = false;
  String? _error;
  String? _registrationSuccess;

  Future<void> _openCreateAccount() async {
    if (_isLoading) return;

    _passwordController.clear();
    setState(() {
      _error = null;
      _registrationSuccess = null;
    });

    final email = await Navigator.of(context).push<String>(
      MaterialPageRoute(
        builder: (_) => CreateAccountScreen(client: widget.client),
      ),
    );

    if (!mounted || email == null) return;
    _emailController.text = email;
    _passwordController.clear();
    setState(() {
      _error = null;
      _registrationSuccess = 'Account created. Sign in with $email.';
    });
  }

  Future<void> _signIn() async {
    if (_isLoading) return;

    setState(() {
      _isLoading = true;
      _error = null;
      _registrationSuccess = null;
    });

    String? accessToken;
    try {
      final response = await widget.client.post(
        Uri.parse('$_baseUrl/auth/login'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({
          'email': _emailController.text,
          'password': _passwordController.text,
        }),
      );
      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);
        if (data is Map<String, dynamic>) {
          final token = data['access_token'];
          if (token is String && token.trim().isNotEmpty) {
            accessToken = token.trim();
          }
        }
      }
    } catch (_) {
      accessToken = null;
    }

    if (!mounted) return;
    if (accessToken != null) {
      _passwordController.clear();
      widget.onAuthenticated(accessToken);
      return;
    }

    _passwordController.clear();
    setState(() {
      _isLoading = false;
      _error = 'Authentication failed.';
    });
  }

  @override
  void dispose() {
    _emailController.dispose();
    _passwordController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(24),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                const Text(
                  'School ERP Agent',
                  style: TextStyle(fontSize: 24, fontWeight: FontWeight.bold),
                ),
                const SizedBox(height: 24),
                TextField(
                  controller: _emailController,
                  keyboardType: TextInputType.emailAddress,
                  textInputAction: TextInputAction.next,
                  decoration: const InputDecoration(
                    labelText: 'Email',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: _passwordController,
                  obscureText: true,
                  autocorrect: false,
                  enableSuggestions: false,
                  decoration: const InputDecoration(
                    labelText: 'Password',
                    border: OutlineInputBorder(),
                  ),
                  onSubmitted: (_) => _signIn(),
                ),
                if (_error != null) ...[
                  const SizedBox(height: 12),
                  Text(_error!, style: const TextStyle(color: Colors.red)),
                ],
                if (_registrationSuccess != null) ...[
                  const SizedBox(height: 12),
                  Text(
                    _registrationSuccess!,
                    style: const TextStyle(color: Colors.green),
                    textAlign: TextAlign.center,
                  ),
                ],
                const SizedBox(height: 16),
                FilledButton(
                  onPressed: _isLoading ? null : _signIn,
                  child: _isLoading
                      ? const SizedBox(
                          width: 20,
                          height: 20,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Text('Sign in'),
                ),
                const SizedBox(height: 8),
                TextButton(
                  onPressed: _isLoading ? null : _openCreateAccount,
                  child: const Text('Create account'),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class CreateAccountScreen extends StatefulWidget {
  const CreateAccountScreen({super.key, required this.client});

  final http.Client client;

  @override
  State<CreateAccountScreen> createState() => _CreateAccountScreenState();
}

class _CreateAccountScreenState extends State<CreateAccountScreen> {
  final TextEditingController _fullNameController = TextEditingController();
  final TextEditingController _passwordController = TextEditingController();
  final TextEditingController _confirmPasswordController =
      TextEditingController();
  final TextEditingController _schoolCodeController = TextEditingController();
  bool _isLoading = false;
  String? _error;

  Future<void> _createAccount() async {
    if (_isLoading) return;

    final fullName = _fullNameController.text.trim();
    final password = _passwordController.text;
    final confirmPassword = _confirmPasswordController.text;
    final schoolCode = _schoolCodeController.text.trim();

    if (fullName.isEmpty ||
        password.isEmpty ||
        confirmPassword.isEmpty ||
        schoolCode.isEmpty) {
      setState(() {
        _error = 'Please complete all fields.';
      });
      return;
    }
    if (password != confirmPassword) {
      setState(() {
        _error = 'Passwords do not match.';
      });
      return;
    }

    setState(() {
      _isLoading = true;
      _error = null;
    });

    String? generatedEmail;
    try {
      final response = await widget.client.post(
        Uri.parse('$_baseUrl/auth/register'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({
          'full_name': fullName,
          'password': password,
          'confirm_password': confirmPassword,
          'school_code': schoolCode,
        }),
      );
      if (response.statusCode == 201) {
        final data = jsonDecode(response.body);
        if (data is Map<String, dynamic> && data['status'] == 'created') {
          final email = data['email'];
          if (email is String && email.trim().isNotEmpty) {
            generatedEmail = email.trim();
          }
        }
      }
    } catch (_) {
      generatedEmail = null;
    }

    if (!mounted) return;
    _passwordController.clear();
    _confirmPasswordController.clear();
    if (generatedEmail != null) {
      Navigator.of(context).pop(generatedEmail);
      return;
    }

    setState(() {
      _isLoading = false;
      _error = 'Account creation failed.';
    });
  }

  @override
  void dispose() {
    _fullNameController.dispose();
    _passwordController.dispose();
    _confirmPasswordController.dispose();
    _schoolCodeController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(24),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                const Text(
                  'Create your account',
                  style: TextStyle(fontSize: 24, fontWeight: FontWeight.bold),
                ),
                const SizedBox(height: 24),
                TextField(
                  controller: _fullNameController,
                  textInputAction: TextInputAction.next,
                  decoration: const InputDecoration(
                    labelText: 'Full Name',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: _passwordController,
                  obscureText: true,
                  autocorrect: false,
                  enableSuggestions: false,
                  textInputAction: TextInputAction.next,
                  decoration: const InputDecoration(
                    labelText: 'Password',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: _confirmPasswordController,
                  obscureText: true,
                  autocorrect: false,
                  enableSuggestions: false,
                  textInputAction: TextInputAction.next,
                  decoration: const InputDecoration(
                    labelText: 'Confirm Password',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: _schoolCodeController,
                  decoration: const InputDecoration(
                    labelText: 'School Code',
                    border: OutlineInputBorder(),
                  ),
                  onSubmitted: (_) => _createAccount(),
                ),
                if (_error != null) ...[
                  const SizedBox(height: 12),
                  Text(_error!, style: const TextStyle(color: Colors.red)),
                ],
                const SizedBox(height: 16),
                FilledButton(
                  onPressed: _isLoading ? null : _createAccount,
                  child: _isLoading
                      ? const SizedBox(
                          width: 20,
                          height: 20,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Text('Create Account'),
                ),
                const SizedBox(height: 8),
                TextButton(
                  onPressed: _isLoading
                      ? null
                      : () => Navigator.of(context).pop(),
                  child: const Text('Back to Login'),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class ChatScreen extends StatefulWidget {
  const ChatScreen({
    super.key,
    required this.client,
    required this.accessToken,
    required this.onAuthenticationRequired,
  });

  final http.Client client;
  final String accessToken;
  final VoidCallback onAuthenticationRequired;

  @override
  State<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends State<ChatScreen> {
  final TextEditingController _controller = TextEditingController();
  final List<Map<String, String>> _messages = [];
  bool _isLoading = false;
  bool _isLoggingOut = false;

  Future<void> sendMessageToAgent(String query) async {
    setState(() {
      _isLoading = true;
    });

    var authenticationLost = false;
    try {
      final response = await widget.client.post(
        Uri.parse('$_baseUrl/chat'),
        headers: {
          'Content-Type': 'application/json',
          'Authorization': 'Bearer ${widget.accessToken}',
        },
        body: jsonEncode({'message': query}),
      );

      if (response.statusCode == 401) {
        authenticationLost = true;
        if (mounted) {
          widget.onAuthenticationRequired();
        }
        return;
      }

      final data = jsonDecode(response.body);
      if (!mounted) return;

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
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _messages.insert(0, {"sender": "Agent", "text": "Connection failed. The Agent server is not running yet."});
      });
    } finally {
      if (mounted && !authenticationLost) {
        setState(() {
          _isLoading = false;
        });
      }
    }
  }

  Future<void> _logout() async {
    if (_isLoggingOut) return;
    setState(() {
      _isLoggingOut = true;
    });

    try {
      await widget.client.post(
        Uri.parse('$_baseUrl/auth/logout'),
        headers: {'Authorization': 'Bearer ${widget.accessToken}'},
      );
    } catch (_) {
      // Local logout still completes when the server cannot be reached.
    } finally {
      if (mounted) {
        widget.onAuthenticationRequired();
      }
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
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('School ERP Assistant'),
        backgroundColor: Colors.blueAccent,
        foregroundColor: Colors.white,
        actions: [
          IconButton(
            tooltip: 'Logout',
            onPressed: _isLoggingOut ? null : _logout,
            icon: const Icon(Icons.logout),
          ),
        ],
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
